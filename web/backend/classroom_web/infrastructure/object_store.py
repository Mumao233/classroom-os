"""S3 adapter with explicit credentials, conditional creation and pinned reads."""
import hashlib
import json
import re
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..domain.media import ObjectUnavailable, StoredObject
from .auth_deadlines import remaining


class S3ObjectStore:
    def __init__(self, *, endpoint: str, bucket: str, region: str,
                 access_key: str, secret_key: str, synthetic_loopback=False):
        parsed = urlsplit(endpoint)
        local = synthetic_loopback and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        if (not (local or parsed.scheme == 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                or not region or not access_key or not secret_key):
            raise ValueError('explicit private object store configuration required')
        self.bucket = bucket
        # Explicit credentials prevent SDK fallback to ambient profiles or metadata services.
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=5,
                          retries={'total_max_attempts': 1}, s3={'addressing_style': 'path'},
                          request_checksum_calculation='when_required', response_checksum_validation='when_required'))

    def _key(self, key):
        if not re.fullmatch(r'media/[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{64}', key):
            raise ValueError('invalid generated object key')

    def validate_private(self):
        try:
            acl = self.client.get_bucket_acl(Bucket=self.bucket)
            if any(grant['Grantee'].get('Type') == 'Group' for grant in acl.get('Grants', [])):
                raise ObjectUnavailable('private bucket required')
            try:
                policy = json.loads(self.client.get_bucket_policy(Bucket=self.bucket)['Policy'])
            except ClientError as error:
                if error.response['Error']['Code'] not in ('NoSuchBucketPolicy','NoSuchPolicy'):
                    raise
                policy = {}
            statements = policy.get('Statement', [])
            if isinstance(statements, dict):
                statements = [statements]
            for statement in statements:
                principal = statement.get('Principal')
                if statement.get('Effect') == 'Allow' and ('*' in json.dumps(principal)):
                    raise ObjectUnavailable('private bucket policy required')
        except (ClientError, BotoCoreError, ValueError):
            raise ObjectUnavailable('private bucket verification failed') from None

    def inspect(self, key):
        self._key(key)
        try:
            reply = self.client.head_object(Bucket=self.bucket, Key=key)
            return StoredObject(key, reply['ETag'], reply.get('VersionId'), reply['ContentLength'], reply['LastModified'])
        except ClientError as error:
            if error.response['ResponseMetadata']['HTTPStatusCode'] == 404:
                return None
            raise ObjectUnavailable('object inspection failed') from None
        except BotoCoreError:
            raise ObjectUnavailable('object inspection failed') from None

    def put(self, key, content, length, sha256):
        self._key(key)
        try:
            content.seek(0)
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content,
                ContentLength=length, Metadata={'sha256': sha256}, IfNoneMatch='*')
        except ClientError as error:
            if error.response['ResponseMetadata']['HTTPStatusCode'] != 412:
                raise ObjectUnavailable('object creation failed') from None
        except BotoCoreError:
            raise ObjectUnavailable('object creation failed') from None
        obj = self.inspect(key)
        if obj is None or obj.byte_length != length:
            raise ObjectUnavailable('object size mismatch')
        digest = hashlib.sha256()
        for first in range(0, length, 64_000):
            remaining()
            digest.update(self.read(obj, first, min(length - 1, first + 63_999)))
        if digest.hexdigest() != sha256:
            raise ObjectUnavailable('object integrity mismatch')
        return obj

    def read(self, obj, first, last):
        remaining()
        self._key(obj.key)
        params = dict(Bucket=self.bucket, Key=obj.key, Range=f'bytes={first}-{last}', IfMatch=obj.etag)
        if obj.version_id:
            params['VersionId'] = obj.version_id
        try:
            reply = self.client.get_object(**params)
            with reply['Body'] as stream:
                content = stream.read(last - first + 2)
            if len(content) != last - first + 1:
                raise ObjectUnavailable('object range mismatch')
            return content
        except (ClientError, BotoCoreError, OSError):
            raise ObjectUnavailable('object read failed') from None
