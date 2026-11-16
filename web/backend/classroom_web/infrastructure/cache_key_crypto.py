"""Versioned cache DEK envelopes, separate from MFA and media key purposes."""

import base64
import json
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..domain.auth import AuthRejected


def envelope_aad(row):
    fields = ("id", "owner_id", "device_id", "purpose", "class_session_id", "key_version")
    values = [str(row[field]) if row[field] is not None else None for field in fields]
    return json.dumps(["classroom-cache-dek-v1", *values], separators=(",", ":")).encode("ascii")


class CacheKeyCrypto:
    def __init__(self, keys, *, active_version):
        self._keys = dict(keys)
        if active_version not in self._keys or any(
            not isinstance(version, str) or not 1 <= len(version) <= 64 or
            not isinstance(key, bytes) or len(key) != 32
            for version, key in self._keys.items()
        ):
            raise ValueError("explicit versioned 256-bit cache KEKs required")
        self.active_version = active_version

    def create(self, metadata):
        material = AESGCM.generate_key(bit_length=256)
        return self.wrap(metadata, material)

    def wrap(self, metadata, material):
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[self.active_version]).encrypt(nonce, material, envelope_aad(metadata))
        # Fixed-size binary envelope: format byte, 96-bit nonce, 32-byte key + tag.
        return dict(wrapped_dek=b"\x01" + nonce + ciphertext, kek_version=self.active_version)

    def unwrap(self, row):
        try:
            envelope = bytes(row["wrapped_dek"])
            if len(envelope) != 61 or envelope[0] != 1:
                raise ValueError("invalid envelope")
            material = AESGCM(self._keys[row["kek_version"]]).decrypt(
                envelope[1:13], envelope[13:], envelope_aad(row))
            if len(material) != 32:
                raise ValueError("invalid DEK")
            return base64.b64encode(material).decode("ascii")
        except (KeyError, ValueError, TypeError):
            raise AuthRejected("STORAGE_UNAVAILABLE", 503) from None
        except Exception:
            raise AuthRejected("STORAGE_UNAVAILABLE", 503) from None
