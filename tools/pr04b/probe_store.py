"""Measure the object-store behaviour PR-04b depends on. Prints one JSON report."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web/backend/tests/integration'))
from botocore.exceptions import ClientError  # noqa: E402
from object_service import ObjectService  # noqa: E402


def attempt(function):
    try:
        return dict(ok=True, value=function())
    except ClientError as error:
        return dict(ok=False, status=error.response['ResponseMetadata']['HTTPStatusCode'],
                    code=error.response['Error'].get('Code'))
    except Exception as error:  # report, never hide
        return dict(ok=False, error=type(error).__name__, detail=str(error)[:200])


def versions(c, bucket, key):
    reply = c.list_object_versions(Bucket=bucket, Prefix=key)
    return dict(versions=[(v.get('VersionId'), v.get('IsLatest')) for v in reply.get('Versions', [])],
                markers=[(v.get('VersionId'), v.get('IsLatest')) for v in reply.get('DeleteMarkers', [])])


def main(directory):
    service = ObjectService(Path(directory), 'probe').start()
    report = {}
    try:
        c = service.client()
        c.create_bucket(Bucket='probe-plain')
        report['first_create'] = attempt(lambda: c.put_object(Bucket='probe-plain', Key='k', Body=b'first', IfNoneMatch='*')['ResponseMetadata']['HTTPStatusCode'])
        report['second_create'] = attempt(lambda: c.put_object(Bucket='probe-plain', Key='k', Body=b'second', IfNoneMatch='*')['ResponseMetadata']['HTTPStatusCode'])
        report['after_second'] = c.get_object(Bucket='probe-plain', Key='k')['Body'].read().decode()
        report['plain_versions'] = attempt(lambda: versions(c, 'probe-plain', 'k'))
        report['plain_delete_null'] = attempt(lambda: c.delete_object(Bucket='probe-plain', Key='k', VersionId='null')['ResponseMetadata']['HTTPStatusCode'])
        report['plain_after_delete'] = attempt(lambda: versions(c, 'probe-plain', 'k'))
        report['plain_head_after'] = attempt(lambda: c.head_object(Bucket='probe-plain', Key='k')['ContentLength'])
        report['missing_get'] = attempt(lambda: c.get_object(Bucket='probe-plain', Key='absent'))
        c.create_bucket(Bucket='probe-versioned')
        report['enable_versioning'] = attempt(lambda: c.put_bucket_versioning(Bucket='probe-versioned', VersioningConfiguration={'Status': 'Enabled'})['ResponseMetadata']['HTTPStatusCode'])
        report['versioning_status'] = attempt(lambda: c.get_bucket_versioning(Bucket='probe-versioned').get('Status'))
        report['v_put_1'] = attempt(lambda: c.put_object(Bucket='probe-versioned', Key='k', Body=b'one').get('VersionId'))
        report['v_put_2'] = attempt(lambda: c.put_object(Bucket='probe-versioned', Key='k', Body=b'two').get('VersionId'))
        report['v_create_only_on_existing'] = attempt(lambda: c.put_object(Bucket='probe-versioned', Key='k', Body=b'three', IfNoneMatch='*').get('VersionId'))
        report['v_listed'] = attempt(lambda: versions(c, 'probe-versioned', 'k'))
        report['v_plain_delete'] = attempt(lambda: c.delete_object(Bucket='probe-versioned', Key='k').get('VersionId'))
        report['v_after_plain_delete'] = attempt(lambda: versions(c, 'probe-versioned', 'k'))
        report['v_head_after_plain_delete'] = attempt(lambda: c.head_object(Bucket='probe-versioned', Key='k')['ContentLength'])
        report['v_create_only_after_marker'] = attempt(lambda: c.put_object(Bucket='probe-versioned', Key='k', Body=b'four', IfNoneMatch='*').get('VersionId'))
        listed = versions(c, 'probe-versioned', 'k')
        report['v_before_erase'] = listed
        removed = []
        for group in ('versions', 'markers'):
            for version, _ in listed[group]:
                removed.append(attempt(lambda v=version: c.delete_object(Bucket='probe-versioned', Key='k', VersionId=v)['ResponseMetadata']['HTTPStatusCode']))
        report['v_delete_each'] = removed
        report['v_after_erase'] = attempt(lambda: versions(c, 'probe-versioned', 'k'))
        report['v_head_after_erase'] = attempt(lambda: c.head_object(Bucket='probe-versioned', Key='k')['ContentLength'])
        report['acl'] = attempt(lambda: [g['Grantee'].get('Type') for g in c.get_bucket_acl(Bucket='probe-plain').get('Grants', [])])
        report['policy'] = attempt(lambda: c.get_bucket_policy(Bucket='probe-plain')['Policy'])
        # Durability across a stop/start on the same data directory.
        c.put_object(Bucket='probe-plain', Key='persist', Body=b'persist')
        began = time.monotonic()
        service.stop()
        report['down_get'] = attempt(lambda: c.get_object(Bucket='probe-plain', Key='persist'))
        service.start()
        report['restart_seconds'] = round(time.monotonic() - began, 1)
        report['after_restart'] = attempt(lambda: service.client().get_object(Bucket='probe-plain', Key='persist')['Body'].read().decode())
    finally:
        service.stop()
    print(json.dumps(report, indent=1, default=str))


if __name__ == '__main__':
    main(sys.argv[1])
