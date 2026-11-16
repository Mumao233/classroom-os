"""Independent deletion ledger: an immutable, hash-chained, create-only S3 object log.

Entry N lives at ``ledger/v1/<N as 20 digits>.json`` and carries the SHA-256 of entry
N-1. An entry is written only with ``If-None-Match: *``, so claiming the next
generation is the compare-and-swap on the head: of two writers exactly one succeeds
and the other re-reads the chain and appends after it. Nothing is ever overwritten.

Readers trust neither a cached head nor a listing alone: the chain is read from
generation 1, every link is verified, and any key listed beyond the first missing
generation is treated as corruption. A store that does not enforce create-only
writes is rejected when the adapter is constructed.
"""
import hashlib
import json
import re
import threading
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
    def __init__(self, *, endpoint: str, bucket: str, region: str, access_key: str,
                 secret_key: str, synthetic_loopback=False, max_entry_bytes=4096):
        parsed = urlsplit(endpoint)
        local = synthetic_loopback and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        if (not (local or parsed.scheme == 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                or not region or not access_key or not secret_key):
            raise ValueError('explicit independent ledger configuration required')
        self.endpoint, self.bucket, self.max_entry_bytes = endpoint.rstrip('/'), bucket, max_entry_bytes
        # Explicit credentials only; one attempt per call so a retry is always our decision.
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=5,
                          retries={'total_max_attempts': 1}, s3={'addressing_style': 'path'},
                          request_checksum_calculation='when_required', response_checksum_validation='when_required'))
        self._lock = threading.Lock()
        self._chain: list[LedgerEntry] = []
        self._by_intent: dict = {}
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

    def _get(self, generation):
        try:
            with self.client.get_object(Bucket=self.bucket, Key=self._name(generation))['Body'] as stream:
                raw = stream.read(self.max_entry_bytes + 1)
        except ClientError as error:
            if _status(error) == 404:
                return None
            raise LedgerUnavailable('ledger entry unreadable') from None
        except (BotoCoreError, OSError):
            raise LedgerUnavailable('ledger entry unreadable') from None
        if len(raw) > self.max_entry_bytes:
            raise LedgerCorrupt('ledger entry exceeds its bound')
        return raw

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

    def _listed_head(self):
        highest, count, token = 0, 0, {}
        try:
            while True:
                reply = self.client.list_objects_v2(Bucket=self.bucket, Prefix=PREFIX, **token)
                for item in reply.get('Contents', []):
                    match = _KEY.fullmatch(item['Key'])
                    if match is None:
                        raise LedgerCorrupt('unexpected object in ledger prefix')
                    highest, count = max(highest, int(match.group(1))), count + 1
                if not reply.get('IsTruncated'):
                    if count != highest:
                        raise LedgerCorrupt('ledger generation gap')
                    return highest
                token = dict(ContinuationToken=reply['NextContinuationToken'])
        except (ClientError, BotoCoreError, KeyError):
            raise LedgerUnavailable('ledger listing failed') from None

    def _extend(self, chain, by_intent):
        """Read forward from the verified prefix until the first missing generation."""
        while True:
            generation = len(chain) + 1
            raw = self._get(generation)
            if raw is None:
                break
            entry = self._parse(raw, generation, chain[-1].entry_hash if chain else GENESIS)
            if entry.intent.intent_id in by_intent:
                raise LedgerCorrupt('duplicate intent in ledger')
            chain.append(entry)
            by_intent[entry.intent.intent_id] = entry
        # A listed key past the first missing generation means an entry was lost.
        if self._listed_head() > len(chain):
            if self._get(len(chain) + 1) is None:
                raise LedgerCorrupt('ledger generation gap')
            return self._extend(chain, by_intent)
        return chain, by_intent

    def entries(self):
        """Fresh full verification from generation 1; never served from the cache."""
        chain, by_intent = self._extend([], {})
        with self._lock:
            if len(chain) < len(self._chain) or any(a.entry_hash != b.entry_hash for a, b in zip(chain, self._chain)):
                raise LedgerCorrupt('ledger history changed')
            self._chain, self._by_intent = chain, by_intent
        return list(chain)

    def append(self, intent: DeletionIntent) -> int:
        """Return the generation holding this intent, appending it if it is new.

        The generation is returned only after the stored bytes were read back.
        """
        if not isinstance(intent, DeletionIntent):
            raise TypeError('a deletion intent is required')
        with self._lock:
            for _ in range(32):
                self._extend(self._chain, self._by_intent)
                known = self._by_intent.get(intent.intent_id)
                if known is not None:
                    if known.intent != intent:
                        raise LedgerConflict('intent identity recorded with different content')
                    return known.generation
                generation = len(self._chain) + 1
                raw = canonical(dict(v=1, generation=generation, intent=intent.record(),
                                     previous=self._chain[-1].entry_hash if self._chain else GENESIS))
                try:
                    self.client.put_object(Bucket=self.bucket, Key=self._name(generation), Body=raw,
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
