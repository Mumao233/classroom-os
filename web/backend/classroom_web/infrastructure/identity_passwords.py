"""Mature Argon2id adapter. Parameters remain TBD-08 experimental initial values."""
from argon2 import PasswordHasher
from argon2.low_level import Type

from ..domain.identity import IdentityRejected


class Argon2PasswordHasher:
    def __init__(self):
        self.component = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1,
                                        hash_len=32, salt_len=16, type=Type.ID)

    def hash(self, password: str, *, role='admin') -> str:
        if role not in ('student','admin') or not isinstance(password, str) or not (6 if role=='student' else 12) <= len(password) <= 1024 or any(0xD800 <= ord(c) <= 0xDFFF for c in password):
            raise IdentityRejected("INVALID_INPUT")
        return self.component.hash(password)
