const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const databaseName = 'pr05a-synthetic-v1';
const aadFields = ['owner', 'device', 'scope', 'object', 'revision', 'schema', 'key_id', 'key_epoch'];

function bytesToBase64(bytes) {
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  return btoa(chunks.join(''));
}

function base64ToBytes(value) {
  return Uint8Array.from(atob(value), character => character.charCodeAt(0));
}

function aad(binding) {
  return encoder.encode(JSON.stringify(aadFields.map(field => binding[field])));
}

function recordId(binding) {
  return JSON.stringify(aadFields.slice(0, 6).map(field => binding[field]));
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore('records', { keyPath: 'id' });
      request.result.createObjectStore('nonces', { keyPath: 'id' });
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('DATABASE_BLOCKED'));
    request.onsuccess = () => resolve(request.result);
  });
}

export class BrowserVault extends EventTarget {
  #key = null;
  #binding = null;
  #context = null;
  #generation = 0;
  #deadline = 0;
  #timer = null;
  #poll = null;
  #channel = new BroadcastChannel('pr05a-lock');

  constructor() {
    super();
    this.pendingText = null;
    this.lastFailure = null;
    this.#channel.onmessage = () => this.lock();
    this.pageHide = () => this.lock();
    window.addEventListener('pagehide', this.pageHide);
    this.#poll = setInterval(() => { void this.verifyGrant(); }, 500);
  }

  async request(path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (body !== undefined) headers['X-CSRF-Token'] = this.#context?.csrf ?? '';
    const response = await fetch(`/lab/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin', cache: 'no-store', headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.code ?? 'REQUEST_FAILED');
    return result;
  }

  async login(alias) {
    this.lock();
    this.#channel.postMessage('lock');
    const generation = this.#generation;
    const bootstrap = await this.request('bootstrap');
    this.#context = { csrf: bootstrap.csrf };
    const context = await this.request('login', { alias });
    if (generation !== this.#generation) throw new Error('STALE_OPERATION');
    this.#context = context;
    return { ...context };
  }

  async unlock(scope = 'drafts', recoveryKey = null) {
    this.lock();
    const generation = this.#generation;
    const context = await this.request('session');
    if (generation !== this.#generation) throw new Error('STALE_OPERATION');
    this.#context = context;
    const response = await this.request(recoveryKey ? 'recover-key' : 'unlock', {
      scope, key_epoch: context.key_epoch, ...(recoveryKey ? { key_id: recoveryKey } : {}),
    });
    const material = base64ToBytes(response.key_material_b64);
    delete response.key_material_b64;
    let key;
    try {
      key = await crypto.subtle.importKey('raw', material, 'AES-GCM', false, ['encrypt', 'decrypt']);
    } finally {
      material.fill(0);
    }
    if (generation !== this.#generation) throw new Error('STALE_OPERATION');
    if (response.owner !== context.owner || response.device !== context.device || response.scope !== scope ||
        response.security_epoch !== context.epoch) throw new Error('KEY_CONTEXT_MISMATCH');
    const remaining = Math.min((response.expires_at * 1000) - Date.now(), 60_000);
    if (remaining <= 0) throw new Error('KEY_EXPIRED');
    this.#key = key;
    this.#deadline = performance.now() + remaining;
    this.#binding = response;
    this.#timer = setTimeout(() => this.lock(), remaining);
    return this.status();
  }

  status() {
    if (this.#key && performance.now() >= this.#deadline) this.lock();
    return {
      unlocked: this.#key !== null, extractable: this.#key?.extractable ?? null,
      binding: this.#binding ? { ...this.#binding } : null,
    };
  }

  #current() {
    if (!this.status().unlocked) throw new Error('LOCKED');
    return this.#generation;
  }

  #check(generation) {
    if (generation !== this.#current()) throw new Error('STALE_OPERATION');
  }

  lock() {
    this.#generation += 1;
    clearTimeout(this.#timer);
    this.#key = null;
    this.#binding = null;
    this.#deadline = 0;
    this.pendingText = null;
    this.dispatchEvent(new Event('locked'));
  }

  async logout() {
    this.lock();
    this.#channel.postMessage('lock');
    try {
      this.#context = await this.request('session');
      await this.request('logout', {});
    } finally { this.#context = null; }
  }

  async verifyGrant() {
    if (!this.status().unlocked) return;
    const generation = this.#generation;
    try {
      await this.request('grant', { grant_id: this.#binding.grant_id });
    } catch (error) {
      // Offline access is bounded by the existing monotonic deadline.
      if (generation === this.#generation && !(error instanceof TypeError)) this.lock();
    }
  }

  async confirmRecovery() {
    this.lock();
    const context = await this.request('session');
    this.#context = context;
    await this.request('confirm-recovery', {});
  }

  async rotateDek() {
    this.lock();
    await this.request('rotate-dek', {});
  }

  async confirmRecording() {
    await this.request('confirm-recording', {});
  }

  async encrypt(text, object, revision = 1, schema = 1) {
    const generation = this.#current();
    if (typeof text !== 'string' || typeof object !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(object) ||
        !Number.isSafeInteger(revision) || revision < 1 || schema !== 1) throw new Error('INVALID_INPUT');
    const binding = {
      owner: this.#binding.owner, device: this.#binding.device, scope: this.#binding.scope,
      object, revision, schema, key_id: this.#binding.key_id, key_epoch: this.#binding.key_epoch,
    };
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const plain = encoder.encode(text);
    let encrypted;
    try {
      encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad(binding), tagLength: 128 }, this.#key, plain);
    } finally {
      plain.fill(0);
    }
    this.#check(generation);
    return {
      id: recordId(binding), binding, nonce: bytesToBase64(nonce),
      ciphertext: bytesToBase64(new Uint8Array(encrypted)),
    };
  }

  async commit(envelope) {
    const generation = this.#current();
    // Snapshot before any asynchronous work so callers cannot mutate a queued write.
    const snapshot = structuredClone(envelope);
    if (!snapshot || Object.keys(snapshot).sort().join(',') !== 'binding,ciphertext,id,nonce' ||
        !snapshot.binding || Object.keys(snapshot.binding).sort().join(',') !== [...aadFields].sort().join(',') ||
        typeof snapshot.binding.object !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(snapshot.binding.object) ||
        !Number.isSafeInteger(snapshot.binding.revision) || snapshot.binding.revision < 1 || snapshot.binding.schema !== 1 ||
        typeof snapshot.nonce !== 'string' || !/^[A-Za-z0-9+/]{16}$/.test(snapshot.nonce) ||
        typeof snapshot.ciphertext !== 'string') throw new Error('INVALID_ENVELOPE');
    if (snapshot.id !== recordId(snapshot.binding) || snapshot.binding.owner !== this.#binding.owner ||
        snapshot.binding.device !== this.#binding.device || snapshot.binding.scope !== this.#binding.scope ||
        snapshot.binding.key_id !== this.#binding.key_id || snapshot.binding.key_epoch !== this.#binding.key_epoch) {
      throw new Error('RECORD_CONTEXT_MISMATCH');
    }
    await this.decrypt(snapshot, snapshot.binding.object, snapshot.binding.revision, snapshot.binding.schema);
    const db = await openDatabase();
    try {
      this.#check(generation);
      await new Promise((resolve, reject) => {
        const transaction = db.transaction(['records', 'nonces'], 'readwrite');
        const records = transaction.objectStore('records');
        let conflict = null;
        const current = records.get(snapshot.id);
        current.onsuccess = () => {
          if (current.result) {
            if (JSON.stringify(current.result) !== JSON.stringify(snapshot)) {
              conflict = new Error('IMMUTABLE_VERSION_CONFLICT');
              transaction.abort();
            }
            return;
          }
          transaction.objectStore('nonces').add({ id: `${snapshot.binding.key_id}/${snapshot.nonce}` });
          records.add(snapshot);
        };
        transaction.oncomplete = () => resolve();
        transaction.onabort = () => reject(conflict ?? transaction.error ?? new Error('TRANSACTION_ABORTED'));
        transaction.onerror = () => {};
      });
      this.#check(generation);
    } finally { db.close(); }
  }

  async save(text, object, revision = 1) {
    this.#current();
    this.pendingText = text;
    try {
      const envelope = await this.encrypt(text, object, revision);
      await this.commit(envelope);
      this.pendingText = null;
      this.lastFailure = null;
      return envelope;
    } catch (error) {
      this.lastFailure = error.name === 'QuotaExceededError' ? 'QUOTA_EXCEEDED' : error.message;
      throw error;
    }
  }

  async load(object, revision = 1, schema = 1) {
    const generation = this.#current();
    const id = recordId({ ...this.#binding, object, revision, schema });
    const db = await openDatabase();
    let envelope;
    try {
      envelope = await new Promise((resolve, reject) => {
        const transaction = db.transaction('records', 'readonly');
        const request = transaction.objectStore('records').get(id);
        transaction.oncomplete = () => resolve(request.result);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally { db.close(); }
    this.#check(generation);
    if (!envelope) throw new Error('LOCAL_COPY_UNAVAILABLE');
    return this.decrypt(envelope, object, revision, schema);
  }

  async decrypt(envelope, object, revision = 1, schema = 1) {
    const generation = this.#current();
    const expected = {
      owner: this.#binding.owner, device: this.#binding.device, scope: this.#binding.scope,
      object, revision, schema, key_id: this.#binding.key_id, key_epoch: this.#binding.key_epoch,
    };
    if (envelope.id !== recordId(expected) || JSON.stringify([...aad(envelope.binding)]) !== JSON.stringify([...aad(expected)])) {
      throw new Error('RECORD_CONTEXT_MISMATCH');
    }
    const plain = new Uint8Array(await crypto.subtle.decrypt({
      name: 'AES-GCM', iv: base64ToBytes(envelope.nonce), additionalData: aad(expected), tagLength: 128,
    }, this.#key, base64ToBytes(envelope.ciphertext)));
    try {
      this.#check(generation);
      return decoder.decode(plain);
    } finally { plain.fill(0); }
  }

  dispose() {
    this.lock();
    clearInterval(this.#poll);
    this.#channel.close();
    window.removeEventListener('pagehide', this.pageHide);
  }
}
