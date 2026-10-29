"""Explicit synthetic-only worker CLI. No environment-selected provider or DB."""
import argparse
import time

from ..application.task_worker import MockProvider, TaskWorker
from ..infrastructure.database import Database
from ..infrastructure.task_scheduler import PgTaskScheduler
from ..test_support import validate_test_database_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-url', required=True)
    parser.add_argument('--scheduler-url', required=True)
    parser.add_argument('--worker-id', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    runtime = validate_test_database_target(args.runtime_url)
    scheduler = validate_test_database_target(args.scheduler_url)
    if (runtime.host, runtime.port, runtime.database) != (scheduler.host, scheduler.port, scheduler.database):
        parser.error('runtime and scheduler must use the same isolated synthetic database')
    database = Database(runtime.render_as_string(hide_password=False))
    queue = PgTaskScheduler(scheduler.render_as_string(hide_password=False))
    worker = TaskWorker(database, queue, MockProvider(), worker_id=args.worker_id)
    try:
        while True:
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        database.dispose()
        queue.dispose()


if __name__ == '__main__':
    main()
