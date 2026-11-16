"""Synthetic fault and concurrency tests. Run inside the VMware guest."""

import base64
import concurrent.futures
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from key_service import KeyService, Rejected


class KeyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.work)
        self.path = Path(self.temp.name) / "keys.sqlite"
        self.service = KeyService(self.path)
        self.token, self.context = self.service.login("A")

    def tearDown(self):
        self.temp.cleanup()

    def unlock(self):
        return self.service.unlock(self.token, "drafts", self.context["key_epoch"])

    def rejects(self, code, function, *args, **kwargs):
        with self.assertRaises(Rejected) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_wrapped_key_only_and_stable_retry(self):
        result = self.unlock()
        key = base64.b64decode(result["key_material_b64"])
        self.assertEqual(len(key), 32)
        self.assertFalse(key in self.path.read_bytes())
        self.assertFalse(result["key_material_b64"].encode() in self.path.read_bytes())
        retry = self.unlock()
        self.assertEqual(result["key_id"], retry["key_id"])
        self.assertTrue(result["key_material_b64"] == retry["key_material_b64"])
        self.assertNotEqual(result["grant_id"], retry["grant_id"])
        with self.service.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM keys").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM grants").fetchone()[0], 2)

    def test_session_csrf_and_stale_epoch(self):
        self.rejects("UNAUTHENTICATED", self.service.unlock, "caller-chosen-owner", "drafts", 1)
        self.rejects("CSRF_REJECTED", self.service.authorize_write, self.token, "wrong")
        self.service.authorize_write(self.token, self.context["csrf"])
        self.rejects("KEY_AUTHORIZATION_REVOKED", self.service.unlock, self.token, "drafts", 2)
        self.rejects("INVALID_INPUT", self.service.unlock, self.token, "drafts", True)

    def test_cross_owner_and_scope(self):
        key = self.unlock()
        token_b, ctx_b = self.service.login("B")
        self.rejects("KEY_NOT_FOUND", self.service.unlock, token_b, "drafts", ctx_b["key_epoch"], recovery_key=key["key_id"])
        self.rejects("KEY_NOT_FOUND", self.service.unlock, self.token, "learning", 1, recovery_key=key["key_id"])

    def test_untrusted_requires_separate_recording_consent(self):
        with self.service.transaction() as db:
            db.execute("UPDATE devices SET trusted=0 WHERE id=?", (self.context["device"],))
        self.rejects("DEVICE_NOT_TRUSTED", self.unlock)
        self.rejects("RECORDING_CONSENT_REQUIRED", self.service.unlock, self.token, "recording_recovery", 1)
        self.service.confirm_recording(self.token)
        self.assertEqual(self.service.unlock(self.token, "recording_recovery", 1)["scope"], "recording_recovery")

    def test_wrapping_aad_and_ciphertext_tampering(self):
        self.unlock()
        with self.service.transaction() as db:
            original = dict(db.execute("SELECT * FROM keys").fetchone())
        for field, value in (("owner", "wrong"), ("device", "wrong"), ("scope", "learning"),
                             ("epoch", 2), ("id", "wrong"), ("wrapped", b"tampered")):
            row = {**original, field: value}
            self.rejects("ENVELOPE_AUTHENTICATION_FAILED", self.service._unwrap, row)

    def test_password_reset_retains_key_and_requires_explicit_recovery(self):
        key = self.unlock()
        self.service.reset_password("A")
        self.rejects("UNAUTHENTICATED", self.unlock)
        token, ctx = self.service.login("A")
        self.rejects("KEY_AUTHORIZATION_REVOKED", self.service.unlock, token, "drafts", ctx["key_epoch"])
        self.service.confirm_recovery(token)
        recovered = self.service.unlock(token, "drafts", ctx["key_epoch"])
        self.assertTrue(key["key_material_b64"] == recovered["key_material_b64"])
        self.assertGreater(ctx["epoch"], self.context["epoch"])

    def test_device_revocation_retains_key_and_invalidates_grants(self):
        key = self.unlock()
        self.service.revoke_device("A")
        self.rejects("UNAUTHENTICATED", self.service.check_grant, self.token, key["grant_id"])
        token, ctx = self.service.login("A")
        self.rejects("KEY_AUTHORIZATION_REVOKED", self.service.unlock, token, "drafts", ctx["key_epoch"])
        self.service.confirm_recovery(token)
        self.assertGreater(ctx["key_epoch"], self.context["key_epoch"])
        current = self.service.unlock(token, "drafts", ctx["key_epoch"])
        self.assertNotEqual(key["key_id"], current["key_id"])
        recovered = self.service.unlock(token, "drafts", ctx["key_epoch"], recovery_key=key["key_id"])
        self.assertEqual(key["key_id"], recovered["key_id"])

    def test_concurrent_unlock_creates_one_key(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.unlock(), range(32)))
        self.assertEqual(len({item["key_id"] for item in results}), 1)
        self.assertEqual(len({item["key_material_b64"] for item in results}), 1)

    def test_interrupted_kek_rotation_rolls_back(self):
        key = self.unlock()
        self.service.unlock(self.token, "learning", 1)
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            self.service.rotate_kek(2, AESGCM.generate_key(bit_length=256), interrupt_after=1)
        self.assertEqual(self.service.active_kek, 1)
        with self.service.transaction() as db:
            self.assertEqual({row[0] for row in db.execute("SELECT kek FROM keys")}, {1})
        self.assertTrue(key["key_material_b64"] == self.unlock()["key_material_b64"])
        self.service.retire_kek(2)
        self.rejects("KEK_STILL_REQUIRED", self.service.retire_kek, 1)

    def test_kek_rotation_concurrent_unlock_and_restart(self):
        before = self.unlock()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.unlock) for _ in range(10)]
            rotation = pool.submit(self.service.rotate_kek, 2, AESGCM.generate_key(bit_length=256))
            rotation.result()
            results = [future.result() for future in futures]
        self.service.retire_kek(1)
        self.assertTrue(all(row["key_material_b64"] == before["key_material_b64"] for row in results))
        restarted = KeyService(self.path, keks=self.service.keks, active_kek=2)
        self.assertTrue(before["key_material_b64"] == restarted.unlock(self.token, "drafts", 1)["key_material_b64"])

    def test_duplicate_concurrent_kek_version_cannot_replace_installed_material(self):
        before = self.unlock()
        def rotate():
            try:
                self.service.rotate_kek(2, AESGCM.generate_key(bit_length=256))
                return "installed"
            except ValueError:
                return "duplicate-rejected"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: rotate(), range(2)))
        self.assertCountEqual(outcomes, ["installed", "duplicate-rejected"])
        self.assertTrue(before["key_material_b64"] == self.unlock()["key_material_b64"])

    def test_missing_and_wrong_kek_fail_without_destroying_envelope(self):
        before = self.unlock()
        material = self.service.keks.pop(1)
        self.rejects("KEK_UNAVAILABLE", self.unlock)
        self.service.keks[1] = AESGCM.generate_key(bit_length=256)
        self.rejects("ENVELOPE_AUTHENTICATION_FAILED", self.unlock)
        self.service.keks[1] = material
        self.assertTrue(before["key_material_b64"] == self.unlock()["key_material_b64"])

    def test_dek_rotation_keeps_historical_ciphertext_key(self):
        old = self.unlock()
        self.service.rotate_dek(self.token)
        self.rejects("KEY_AUTHORIZATION_REVOKED", self.service.check_grant, self.token, old["grant_id"])
        self.rejects("KEY_AUTHORIZATION_REVOKED", self.unlock)
        new = self.service.unlock(self.token, "drafts", 2)
        self.assertFalse(old["key_material_b64"] == new["key_material_b64"])
        recovered = self.service.unlock(self.token, "drafts", 2, recovery_key=old["key_id"])
        self.assertTrue(old["key_material_b64"] == recovered["key_material_b64"])

    def test_full_sqlite_rolls_back_and_preserves_existing_key(self):
        before = self.unlock()
        with self.service.transaction() as db:
            db.execute("CREATE TABLE filler (payload BLOB)")
        with closing(sqlite3.connect(self.path)) as db:
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={pages}")
            with self.assertRaises(sqlite3.OperationalError) as caught:
                db.execute("INSERT INTO filler VALUES (zeroblob(1048576))")
            self.assertIn("full", str(caught.exception))
        self.assertTrue(before["key_material_b64"] == self.unlock()["key_material_b64"])


if __name__ == "__main__":
    raise SystemExit("Use run_experiment.py inside VMware")
