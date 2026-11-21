"""Periodic cleanup and content-free escalation for the portable worker."""
import logging
import time

from ..domain.media import ObjectUnavailable


class PortableMaintenance:
    def __init__(self, transfers, deletion_worker, *, interval=30, logger=None):
        if deletion_worker.queue is not transfers.deletion.queue or deletion_worker.store is not transfers.media.store:
            raise ValueError('portable cleanup must share the deletion queue and object store')
        if not 1 <= interval <= 300:
            raise ValueError('invalid portable maintenance interval')
        self.transfers = transfers
        self.worker = deletion_worker
        self.interval = interval
        self.logger = logger or logging.getLogger('classroom_web.portable')
        self.next_tick = 0

    def tick(self):
        now = time.monotonic()
        if now < self.next_tick:
            return None
        self.next_tick = now + self.interval
        queue = self.worker.queue
        initial = queue.portable_maintenance()
        if initial['alerts']:
            self.logger.warning('Portable object erasure overdue. count=%d', initial['alerts'])
        if initial['incidents']:
            self.logger.critical('Portable object erasure incident. count=%d', initial['incidents'])
        self.transfers.deletion.expire_due(batch=50, rounds=2)
        self.worker.drain(limit=100)
        failed = 0
        settle = self.transfers.deletion.settle_seconds
        for row in queue.portable_import_cleanup(settle):
            with queue.portable_erasing(row['id'], settle) as (key, confirm):
                if key is None:
                    continue
                try:
                    self.worker.store.erase(key)
                except ObjectUnavailable:
                    failed += 1
                else:
                    confirm()
        if failed:
            self.logger.warning('Portable staging erasure will retry. count=%d', failed)
        return queue.portable_maintenance()
