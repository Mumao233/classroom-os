"""Transactional synthetic key service for PR-05a, isolated from production identity."""

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCOPES = {"learning", "drafts", "recording_recovery"}


class Rejected(Exception):
    def __init__(self, code, status=403):
        self.code = code
        self.status = status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def wrapping_aad(row):
    return json.dumps(
        [row["owner"], row["device"], row["scope"], row["epoch"], row["id"]],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


class KeyService:
    """Only synthetic users A/B exist. KEKs stay in the fixture process memory."""

    def __init__(self, database, *, keks=None, active_kek=1, window_seconds=30):
        if not 0.1 <= window_seconds <= 60:
            raise ValueError("POC window must be between 0.1 and 60 seconds")
        self.database = Path(database)
        self._lock = threading.RLock()
        self.keks = dict(keks or {1: AESGCM.generate_key(bit_length=256)})
        self.active_kek = active_kek
        self.window_seconds = window_seconds
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL, epoch INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
                    trusted INTEGER NOT NULL, revoked INTEGER NOT NULL,
                    key_epoch INTEGER NOT NULL, recovery_required INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
                    device TEXT NOT NULL REFERENCES devices(id), epoch INTEGER NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS keys (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, device TEXT NOT NULL,
                    scope TEXT NOT NULL, epoch INTEGER NOT NULL, kek INTEGER NOT NULL,
                    nonce BLOB NOT NULL, wrapped BLOB NOT NULL,
                    UNIQUE(owner, device, scope, epoch));
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY, session_hash TEXT NOT NULL,
                    key_id TEXT NOT NULL REFERENCES keys(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS recording_consents (
                    session_hash TEXT PRIMARY KEY, confirmed INTEGER NOT NULL);
            """)
            for alias in ("A", "B"):
                user = db.execute("SELECT id FROM users WHERE alias=?", (alias,)).fetchone()
                if user is None:
                    owner, device = str(uuid4()), str(uuid4())
                    db.execute("INSERT INTO users VALUES (?, ?, 1)", (owner, alias))
                    db.execute("INSERT INTO devices VALUES (?, ?, 1, 0, 1, 0)", (device, owner))

    @contextmanager
    def transaction(self):
        with self._lock, closing(sqlite3.connect(self.database, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def login(self, alias):
        if alias not in ("A", "B"):
            raise Rejected("SYNTHETIC_ACCOUNT_REQUIRED", 400)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.transaction() as db:
            row = db.execute(
                "SELECT u.id owner,u.epoch,d.id device FROM users u "
                "JOIN devices d ON d.owner=u.id WHERE alias=?", (alias,),
            ).fetchone()
            # Re-login is permitted after revocation, but does not restore key grants.
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                       (digest(token), row["owner"], row["device"], row["epoch"], csrf, time.time() + 600))
        return token, self.context(token)

    def _context(self, db, token):
        if not isinstance(token, str) or not token or len(token) > 128 or not token.isascii():
            raise Rejected("UNAUTHENTICATED", 401)
        row = db.execute(
            "SELECT s.*,u.epoch current_epoch,d.key_epoch,d.trusted,d.revoked,d.recovery_required "
            "FROM sessions s JOIN users u ON u.id=s.owner JOIN devices d ON d.id=s.device "
            "AND d.owner=s.owner WHERE token_hash=?", (digest(token),),
        ).fetchone()
        if row is None or row["epoch"] != row["current_epoch"] or row["expires"] <= time.time():
            raise Rejected("UNAUTHENTICATED", 401)
        return row

    def context(self, token):
        with self.transaction() as db:
            row = self._context(db, token)
            return {name: row[name] for name in
                    ("owner", "device", "epoch", "key_epoch", "csrf", "revoked", "recovery_required")}

    def authorize_write(self, token, csrf):
        with self.transaction() as db:
            row = self._context(db, token)
            if not isinstance(csrf, str) or not secrets.compare_digest(row["csrf"], csrf):
                raise Rejected("CSRF_REJECTED")

    def logout(self, token):
        with self.transaction() as db:
            db.execute("DELETE FROM grants WHERE session_hash=?", (digest(token),))
            db.execute("DELETE FROM recording_consents WHERE session_hash=?", (digest(token),))
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))

    def _unwrap(self, row):
        kek = self.keks.get(row["kek"])
        if kek is None:
            raise Rejected("KEK_UNAVAILABLE", 503)
        try:
            return AESGCM(kek).decrypt(row["nonce"], row["wrapped"], wrapping_aad(row))
        except Exception:
            raise Rejected("ENVELOPE_AUTHENTICATION_FAILED", 503) from None

    def unlock(self, token, scope, key_epoch, *, recovery_key=None):
        if not isinstance(scope, str) or scope not in SCOPES or type(key_epoch) is not int:
            raise Rejected("INVALID_INPUT", 400)
        if recovery_key is not None and (not isinstance(recovery_key, str) or len(recovery_key) != 36):
            raise Rejected("INVALID_INPUT", 400)
        with self.transaction() as db:
            ctx = self._context(db, token)
            if ctx["revoked"] or ctx["recovery_required"] or key_epoch != ctx["key_epoch"]:
                raise Rejected("KEY_AUTHORIZATION_REVOKED")
            if not ctx["trusted"] and scope != "recording_recovery":
                raise Rejected("DEVICE_NOT_TRUSTED")
            if scope == "recording_recovery" and not db.execute(
                "SELECT 1 FROM recording_consents WHERE session_hash=? AND confirmed=1", (digest(token),)
            ).fetchone():
                raise Rejected("RECORDING_CONSENT_REQUIRED")
            if recovery_key:
                row = db.execute("SELECT * FROM keys WHERE id=? AND owner=? AND device=? AND scope=?",
                                 (recovery_key, ctx["owner"], ctx["device"], scope)).fetchone()
                if row is None:
                    raise Rejected("KEY_NOT_FOUND", 404)
            else:
                row = db.execute("SELECT * FROM keys WHERE owner=? AND device=? AND scope=? AND epoch=?",
                                 (ctx["owner"], ctx["device"], scope, key_epoch)).fetchone()
            if row is None:
                metadata = dict(id=str(uuid4()), owner=ctx["owner"], device=ctx["device"],
                                scope=scope, epoch=key_epoch)
                material = AESGCM.generate_key(bit_length=256)
                nonce = secrets.token_bytes(12)
                if self.active_kek not in self.keks:
                    raise Rejected("KEK_UNAVAILABLE", 503)
                wrapped = AESGCM(self.keks[self.active_kek]).encrypt(nonce, material, wrapping_aad(metadata))
                db.execute("INSERT INTO keys VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (*metadata.values(), self.active_kek, nonce, wrapped))
                row = db.execute("SELECT * FROM keys WHERE id=?", (metadata["id"],)).fetchone()
            material = self._unwrap(row)
            expires = min(time.time() + self.window_seconds, ctx["expires"])
            grant = str(uuid4())
            db.execute("INSERT INTO grants VALUES (?, ?, ?, ?)",
                       (grant, digest(token), row["id"], expires))
            return dict(key_id=row["id"], key_epoch=row["epoch"],
                        key_material_b64=base64.b64encode(material).decode("ascii"),
                        expires_at=expires, scope=scope, grant_id=grant,
                        owner=ctx["owner"], device=ctx["device"], security_epoch=ctx["epoch"],
                        cache_policy_revision="pr05a-experiment-only")

    def check_grant(self, token, grant):
        with self.transaction() as db:
            ctx = self._context(db, token)
            row = db.execute("SELECT expires FROM grants WHERE id=? AND session_hash=?",
                             (grant, digest(token))).fetchone()
            if ctx["revoked"] or ctx["recovery_required"] or row is None or row["expires"] <= time.time():
                raise Rejected("KEY_AUTHORIZATION_REVOKED")
            return {"active": True}

    def reset_password(self, alias):
        """Synthetic control only, bypasses the unrelated password delivery flow."""
        with self.transaction() as db:
            owner = db.execute("SELECT id FROM users WHERE alias=?", (alias,)).fetchone()[0]
            db.execute("UPDATE users SET epoch=epoch+1 WHERE id=?", (owner,))
            db.execute("UPDATE devices SET recovery_required=1 WHERE owner=?", (owner,))
            db.execute("DELETE FROM grants WHERE session_hash IN (SELECT token_hash FROM sessions WHERE owner=?)", (owner,))
            db.execute("DELETE FROM sessions WHERE owner=?", (owner,))

    def revoke_device(self, alias):
        with self.transaction() as db:
            owner = db.execute("SELECT id FROM users WHERE alias=?", (alias,)).fetchone()[0]
            db.execute("UPDATE devices SET revoked=1,recovery_required=1,key_epoch=key_epoch+1 WHERE owner=?", (owner,))
            db.execute("DELETE FROM grants WHERE session_hash IN (SELECT token_hash FROM sessions WHERE owner=?)", (owner,))
            db.execute("DELETE FROM sessions WHERE owner=?", (owner,))

    def confirm_recovery(self, token):
        """Lab-only explicit recovery after a new login. Product proof remains TBD-03."""
        with self.transaction() as db:
            ctx = self._context(db, token)
            db.execute("UPDATE devices SET revoked=0,recovery_required=0 WHERE id=? AND owner=?",
                       (ctx["device"], ctx["owner"]))

    def confirm_recording(self, token):
        with self.transaction() as db:
            self._context(db, token)
            db.execute("INSERT OR REPLACE INTO recording_consents VALUES (?, 1)", (digest(token),))

    def rotate_dek(self, token):
        with self.transaction() as db:
            ctx = self._context(db, token)
            if ctx["revoked"] or ctx["recovery_required"]:
                raise Rejected("KEY_AUTHORIZATION_REVOKED")
            db.execute("UPDATE devices SET key_epoch=key_epoch+1 WHERE id=?", (ctx["device"],))
            db.execute("DELETE FROM grants WHERE session_hash IN (SELECT token_hash FROM sessions WHERE device=?)", (ctx["device"],))

    def rotate_kek(self, version, material, *, interrupt_after=None):
        """Install first, atomically rewrap all envelopes, then select the new KEK."""
        with self._lock:
            if type(version) is not int or version <= self.active_kek or version in self.keks or not isinstance(material, bytes) or len(material) != 32:
                raise ValueError("new 256-bit KEK version required")
            self.keks[version] = material
            with self.transaction() as db:
                for index, row in enumerate(db.execute("SELECT * FROM keys ORDER BY id").fetchall(), 1):
                    plain = self._unwrap(row)
                    nonce = secrets.token_bytes(12)
                    wrapped = AESGCM(material).encrypt(nonce, plain, wrapping_aad(row))
                    db.execute("UPDATE keys SET kek=?,nonce=?,wrapped=? WHERE id=?",
                               (version, nonce, wrapped, row["id"]))
                    if interrupt_after == index:
                        raise RuntimeError("synthetic rotation interruption")
            self.active_kek = version

    def retire_kek(self, version):
        with self.transaction() as db:
            if version == self.active_kek or db.execute("SELECT 1 FROM keys WHERE kek=?", (version,)).fetchone():
                raise Rejected("KEK_STILL_REQUIRED", 409)
            self.keks.pop(version, None)
