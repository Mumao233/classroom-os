"""Explicit synthetic-only worker CLI. No environment-selected provider or DB."""
import argparse
import json
from pathlib import Path
from secrets import token_bytes
from urllib.parse import urlsplit
import time

from ..application.task_worker import MockProvider, TaskWorker
from ..infrastructure.database import Database
from ..infrastructure.local_reconciler import PgLocalResultReconciler
from ..infrastructure.task_scheduler import PgTaskScheduler
from ..test_support import validate_test_database_target


def portable_worker(database, queue, worker_id, config):
    from ..application.deletion import DeletionService
    from ..application.media import MediaService
    from ..application.portable_jobs import PortableJobs, PortableTaskWorker
    from ..application.portable_transfers import PortableTransfers
    from ..infrastructure.deletion_ledger import S3DeletionLedger
    from ..infrastructure.deletion_repository import PgDeletionQueue
    from ..infrastructure.object_store import S3ObjectStore

    target = validate_test_database_target(database.engine.url.render_as_string(hide_password=False))
    cleanup = validate_test_database_target(config['deletion_queue_url'])
    if (target.host,target.port,target.database) != (cleanup.host,cleanup.port,cleanup.database):
        raise ValueError('portable worker identities must identify the same synthetic database')
    spool = Path(config['spool_directory'])
    if not spool.is_absolute() or not spool.is_dir():
        raise ValueError('an explicit existing portable spool directory is required')
    for name in ('media','ledger'):
        parsed = urlsplit(config[name]['endpoint'])
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
            raise ValueError('portable storage and ledger must both be synthetic loopback endpoints')
    store = S3ObjectStore(**config['media'], synthetic_loopback=True)
    ledger = S3DeletionLedger(**config['ledger'], synthetic_loopback=True)
    deletion_queue = PgDeletionQueue(cleanup.render_as_string(hide_password=False))
    try:
        media = MediaService(auth=None, database=database, store=store)
        deletion = DeletionService(auth=None, database=database, queue=deletion_queue, store=store,
                                   ledger=ledger, confirmation_secret=token_bytes(32))
        deletion.reconcile()
        worker = PortableTaskWorker(PortableJobs(PortableTransfers(media,deletion,spool)), queue,
                                    worker_id=worker_id)
        return worker, deletion_queue
    except Exception:
        deletion_queue.dispose()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-url', required=True)
    parser.add_argument('--scheduler-url', required=True)
    parser.add_argument('--worker-id', required=True)
    parser.add_argument('--portable-config', type=Path, help='explicit isolated portable worker settings')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    runtime = validate_test_database_target(args.runtime_url)
    scheduler = validate_test_database_target(args.scheduler_url)
    if (runtime.host, runtime.port, runtime.database) != (scheduler.host, scheduler.port, scheduler.database):
        parser.error('runtime and scheduler must use the same isolated synthetic database')
    database = Database(runtime.render_as_string(hide_password=False))
    queue = PgTaskScheduler(scheduler.render_as_string(hide_password=False))
    cleanup = None
    try:
        if args.portable_config is None:
            worker = TaskWorker(database, queue, MockProvider(), worker_id=args.worker_id,
                                reconciler=PgLocalResultReconciler(database))
        else:
            try:
                config = json.loads(args.portable_config.read_text(encoding='utf8'))
                worker, cleanup = portable_worker(database, queue, args.worker_id, config)
            except (OSError, ValueError, TypeError, KeyError):
                parser.error('invalid explicit portable configuration')
        while True:
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if cleanup is not None:
            cleanup.dispose()
        database.dispose()
        queue.dispose()


if __name__ == '__main__':
    main()
