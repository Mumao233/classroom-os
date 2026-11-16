"""Hard-exit checkpoints used only by PR-11a synthetic PostgreSQL tests."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'web/backend'))
from classroom_web.domain.billing import Usage
from classroom_web.domain.data import OwnerContext
from classroom_web.infrastructure.billing_repository import PgBillingRepository
from classroom_web.infrastructure.database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('settlement-before-commit','settlement-after-commit','sent-after-commit'))
    parser.add_argument('owner',type=UUID)
    parser.add_argument('reservation',type=UUID)
    parser.add_argument('--at')
    parser.add_argument('--journal',type=Path)
    args = parser.parse_args()
    database = Database(os.environ['PR11A_RUNTIME_URL'],cursor_secret=b'pr11a-synthetic-cursor-key-00000000')
    with database.transaction(OwnerContext(args.owner)) as transaction:
        repository = PgBillingRepository(transaction)
        row = repository._get(args.reservation)
        if args.stage == 'sent-after-commit':
            repository._now = lambda: datetime.fromisoformat(args.at)
            task = transaction.tasks.get(row['task_id'])
            attempt = transaction.tasks.prepare_attempt(task.id,task.lease_token,task.leased_by)
            repository.authorize_send(row['id'],attempt.id,task.lease_token,task.leased_by)
        else:
            repository.reconcile(row['id'],Usage(provider_request_id=str(row['id']),meter='total',revision=1,
                quantities={'input_tokens':40},final=True))
            if args.stage == 'settlement-before-commit':
                os._exit(73)
    if args.stage == 'sent-after-commit':
        with args.journal.open('a',encoding='utf-8') as stream:
            stream.write(str(args.reservation)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    os._exit(73)


if __name__ == '__main__':
    main()
