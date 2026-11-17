"""Independent deletion ledger: an immutable, hash-chained, create-only S3 object log.

Entry N lives at ``ledger/v1/<N as 20 digits>.json`` and carries the SHA-256 of entry
N-1. An entry is written only with ``If-None-Match: *``, so claiming the next
generation is the compare-and-swap on the head: of two writers exactly one succeeds
and the other re-reads the chain and appends after it. Nothing is ever overwritten.

Readers trust neither a cached head nor a listing alone: the chain is read from
generation 1, every link is verified, and any key listed beyond the first missing
generation is treated as corruption. A store that does not enforce create-only
writes is rejected when the adapter is constructed.

A long-running writer keeps the verified chain in memory. Before every append it
compares the store's listing with the ETag each cached entry had when it was read, so
an entry rewritten or removed underneath it is refused instead of being built upon.

Loss of the newest entries leaves a shorter chain that is still internally valid.
Nothing inside this store can reveal that, so ``verify_head`` compares the chain with
a head witness (generation and entry hash) that the operator recorded elsewhere.
"""
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..domain.deletion import (DeletionIntent, LedgerConflict, LedgerCorrupt, LedgerEntry,
                               LedgerUnavailable, canonical)

PREFIX = 'ledger/v1/'
GENESIS = '0' * 64
_KEY = re.compile(r'ledger/v1/([0-9]{20})\.json')


def _status(error):
    return error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')


class S3DeletionLedger:
    # Longest a single store call may take under a caller's deadline; callers reserve this much.
    CONNECT_SECONDS, READ_SECONDS = 1, 1.5
    call_seconds = CONNECT_SECONDS + READ_SECONDS

    def __init__(self, *, endpoint: str, bucket: str, region: str, access_key: str,
                 secret_key: str, synthetic_loopback=False, max_entry_bytes=262144):
        parsed = urlsplit(endpoint)
        local = synthetic_loopback and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        if (not (local or parsed.scheme == 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                or not region or not access_key or not secret_key):
            raise ValueError('explicit independent ledger configuration required')
        self.endpoint, self.bucket, self.max_entry_bytes = endpoint.rstrip('/'), bucket, max_entry_bytes
        # Explicit credentials only; one attempt per call so a retry is always our decision.
        def connect(connect_timeout, read_timeout):
            return boto3.client('s3', endpoint_url=endpoint, region_name=region,
                aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                config=Config(signature_version='s3v4', connect_timeout=connect_timeout, read_timeout=read_timeout,
                              retries={'total_max_attempts': 1}, s3={'addressing_style': 'path'},
                              request_checksum_calculation='when_required', response_checksum_validation='when_required'))
        self.client = connect(2, 5)
        # Used only while a caller's deadline is set: a slow store is then treated as
        # unavailable soon enough for the caller to still revoke access locally.
        self._bounded = connect(self.CONNECT_SECONDS, self.READ_SECONDS)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._chain: list[LedgerEntry] = []
        self._by_intent: dict = {}
        self._etags: dict = {}
        self._verify_private()
        self._verify_conditional_writes()

    # -- construction checks -------------------------------------------------
    def _verify_private(self):
        try:
            acl = self.client.get_bucket_acl(Bucket=self.bucket)
            if any(grant['Grantee'].get('Type') == 'Group' for grant in acl.get('Grants', [])):
                raise LedgerUnavailable('private ledger bucket required')
            try:
                policy = json.loads(self.client.get_bucket_policy(Bucket=self.bucket)['Policy'])
            except ClientError as error:
                if error.response['Error']['Code'] not in ('NoSuchBucketPolicy', 'NoSuchPolicy'):
                    raise
                policy = {}
            statements = policy.get('Statement', [])
            if isinstance(statements, dict):
                statements = [statements]
            if any(s.get('Effect') == 'Allow' and '*' in json.dumps(s.get('Principal')) for s in statements):
                raise LedgerUnavailable('private ledger bucket policy required')
        except (ClientError, BotoCoreError, ValueError, KeyError):
            raise LedgerUnavailable('ledger bucket verification failed') from None

    def _verify_conditional_writes(self):
        """The store must refuse a second create of an existing key and keep the first bytes."""
        key = 'probe/' + uuid4().hex
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=b'first', IfNoneMatch='*')
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=b'second', IfNoneMatch='*')
            except ClientError as error:
                if _status(error) != 412:
                    raise
            else:
                raise LedgerUnavailable('ledger store does not enforce create-only writes')
            with self.client.get_object(Bucket=self.bucket, Key=key)['Body'] as stream:
                if stream.read(16) != b'first':
                    raise LedgerUnavailable('ledger store overwrote an existing object')
        except (ClientError, BotoCoreError, OSError):
            raise LedgerUnavailable('ledger conditional write verification failed') from None

    # -- chain access ----------------------------------------------------------
    @staticmethod
    def _name(generation):
        return f'{PREFIX}{generation:020d}.json'

    def _store(self):
        """The client for the next chain call. No call starts once the caller's deadline is reached."""
        deadline = getattr(self._local, 'deadline', None)
        if deadline is None:
            return self.client
        if time.monotonic() >= deadline:
            raise LedgerUnavailable('ledger time budget exhausted')
        return self._bounded

    def _fetch(self, generation):
        """(bytes, ETag) of one entry, or (None, None) when the store has no such key."""
        store = self._store()
        try:
            reply = store.get_object(Bucket=self.bucket, Key=self._name(generation))
            with reply['Body'] as stream:
                raw = stream.read(self.max_entry_bytes + 1)
            etag = reply['ETag'].strip('"')
        except ClientError as error:
            if _status(error) == 404:
                return None, None
            raise LedgerUnavailable('ledger entry unreadable') from None
        except (BotoCoreError, OSError, KeyError):
            raise LedgerUnavailable('ledger entry unreadable') from None
        if len(raw) > self.max_entry_bytes:
            raise LedgerCorrupt('ledger entry exceeds its bound')
        return raw, etag

    def _get(self, generation):
        return self._fetch(generation)[0]

    @staticmethod
    def _parse(raw, generation, previous):
        try:
            value = json.loads(raw.decode('ascii'))
            if (not isinstance(value, dict) or set(value) != {'v', 'generation', 'previous', 'intent'}
                    or value['v'] != 1 or value['generation'] != generation or value['previous'] != previous
                    or canonical(value) != raw):
                raise ValueError()
            return LedgerEntry(generation, DeletionIntent.from_record(value['intent']), hashlib.sha256(raw).hexdigest())
        except (ValueError, UnicodeError, KeyError, TypeError):
            raise LedgerCorrupt('ledger chain verification failed') from None

    def _listed(self):
        """{generation: ETag} of every stored entry; a gap or a foreign key is corruption."""
        found, token = {}, {}
        try:
            while True:
                reply = self._store().list_objects_v2(Bucket=self.bucket, Prefix=PREFIX, **token)
                for item in reply.get('Contents', []):
                    match = _KEY.fullmatch(item['Key'])
                    if match is None:
                        raise LedgerCorrupt('unexpected object in ledger prefix')
                    found[int(match.group(1))] = item['ETag'].strip('"')
                if not reply.get('IsTruncated'):
                    if len(found) != max(found, default=0):
                        raise LedgerCorrupt('ledger generation gap')
                    return found
                token = dict(ContinuationToken=reply['NextContinuationToken'])
        except (ClientError, BotoCoreError, KeyError):
            raise LedgerUnavailable('ledger listing failed') from None

    def _extend(self, chain, by_intent, etags):
        """Read forward from the verified prefix until the first missing generation,
        then check the whole prefix, old and new, against what the store lists now."""
        while True:
            generation = len(chain) + 1
            raw, etag = self._fetch(generation)
            if raw is None:
                break
            entry = self._parse(raw, generation, chain[-1].entry_hash if chain else GENESIS)
            if entry.intent.intent_id in by_intent:
                raise LedgerCorrupt('duplicate intent in ledger')
            chain.append(entry)
            by_intent[entry.intent.intent_id] = entry
            etags[generation] = etag
        listed = self._listed()
        # Every entry this reader verified must still be stored with the bytes it verified.
        if any(listed.get(generation) != etag for generation, etag in etags.items()):
            raise LedgerCorrupt('ledger history changed')
        # A listed key past the first missing generation means an entry was lost.
        if len(listed) > len(chain):
            if self._get(len(chain) + 1) is None:
                raise LedgerCorrupt('ledger generation gap')
            return self._extend(chain, by_intent, etags)
        return chain, by_intent, etags

    def entries(self):
        """Fresh full verification from generation 1; never served from the cache."""
        # Under the append lock: a concurrent append can then neither make this snapshot
        # look truncated against the cache nor interleave with the cache update.
        with self._lock:
            chain, by_intent, etags = self._extend([], {}, {})
            if len(chain) < len(self._chain) or any(a.entry_hash != b.entry_hash for a, b in zip(chain, self._chain)):
                raise LedgerCorrupt('ledger history changed')
            self._chain, self._by_intent, self._etags = chain, by_intent, etags
        return list(chain)

    def head(self):
        """(generation, entry hash) of the verified chain; (0, GENESIS) when it is empty.

        Record it outside this store with every backup: it is the only evidence that
        can later show the ledger itself lost its newest entries.
        """
        chain = self.entries()
        return (len(chain), chain[-1].entry_hash) if chain else (0, GENESIS)

    def verify_head(self, witness):
        """Fail unless the chain still contains the witnessed head."""
        try:
            generation, entry_hash = witness
            if type(generation) is not int or generation < 0 or not re.fullmatch(r'[0-9a-f]{64}', entry_hash):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('a ledger head witness is (generation, entry hash)') from None
        chain = self.entries()
        found = GENESIS if generation == 0 else chain[generation - 1].entry_hash if generation <= len(chain) else None
        if found != entry_hash:
            raise LedgerCorrupt('ledger is shorter than or differs from its witnessed head')
        return chain

    def append(self, intent: DeletionIntent, *, deadline: float | None = None) -> LedgerEntry:
        """Return the entry holding this intent, appending it if it is new.

        The entry is returned only after the stored bytes were read back. ``deadline``
        is a ``time.monotonic()`` instant after which no further store call is started.
        """
        if not isinstance(intent, DeletionIntent):
            raise TypeError('a deletion intent is required')
        # Waiting for another append counts against the caller's deadline too.
        if not self._lock.acquire(timeout=-1 if deadline is None else max(0, deadline - time.monotonic())):
            raise LedgerUnavailable('ledger busy past the time budget')
        try:
            self._local.deadline = deadline
            return self._append(intent)
        finally:
            self._local.deadline = None
            self._lock.release()

    def _append(self, intent):
        for _ in range(32):
            self._extend(self._chain, self._by_intent, self._etags)
            known = self._by_intent.get(intent.intent_id)
            if known is not None:
                if known.intent.identity() != intent.identity():
                    raise LedgerConflict('intent identity recorded with different content')
                # The cached entry is evidence only while the store still holds it.
                raw = self._get(known.generation)
                if raw is None or hashlib.sha256(raw).hexdigest() != known.entry_hash:
                    raise LedgerCorrupt('ledger history changed')
                return known
            generation = len(self._chain) + 1
            raw = canonical(dict(v=1, generation=generation, intent=intent.record(),
                                 previous=self._chain[-1].entry_hash if self._chain else GENESIS))
            if len(raw) > self.max_entry_bytes:
                raise ValueError('deletion intent exceeds the ledger entry bound')
            store = self._store()
            try:
                store.put_object(Bucket=self.bucket, Key=self._name(generation), Body=raw,
                                       ContentLength=len(raw), IfNoneMatch='*')
            except ClientError as error:
                if _status(error) == 412:
                    continue  # another writer took this generation; re-read and append after it
                raise LedgerUnavailable('ledger append failed') from None
            except BotoCoreError:
                # The write may or may not have landed. The caller retries with the
                # same intent, and the next read of the chain settles which it was.
                raise LedgerUnavailable('ledger append outcome unknown') from None
            if self._get(generation) != raw:
                raise LedgerUnavailable('ledger append could not be confirmed')
        raise LedgerUnavailable('ledger head contention')
