"""Billing policy and bounded measurements, independent of providers and storage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
from types import MappingProxyType
from uuid import UUID
from zoneinfo import ZoneInfo


MICROYUAN = 1_000_000
MAX_INTEGER = 2**63 - 1
METERS = frozenset(('input_tokens', 'output_tokens', 'audio_duration_ms', 'image_count'))
PURPOSES = MappingProxyType({
    'asr_live': ('asr_streaming', 'asr', 'asr_month_ms'),
    'asr_retranscribe': ('asr_filetrans', 'filetrans', 'retranscribe_month_ms'),
    'ai_question': ('ai_interaction', 'ai', 'interactive_month_count'),
    'ai_explanation': ('ai_interaction', 'ai', 'interactive_month_count'),
    'review_summary': ('ai_summary', 'ai', 'summary_month_count'),
})
SWITCHES = frozenset(('billing', 'registration', 'new_capture', 'asr_streaming',
                      'asr_filetrans', 'ai_interaction', 'ai_summary'))


class BillingBlocked(Exception):
    """Admission failed closed. An existing operation key remains reusable."""


class QuotaExceeded(BillingBlocked):
    def __init__(self, *, resource):
        super().__init__('QUOTA_EXCEEDED')
        self.resource = resource


class UsageConflict(Exception):
    """The same usage identity was supplied with different measurements."""


def integer(value: int, label: str = 'quantity') -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError(f'{label} must be a nonnegative signed bigint')
    return value


def period(at: datetime, kind: str = 'month') -> tuple[datetime, datetime]:
    if at.tzinfo is None or kind not in ('day', 'month'):
        raise ValueError('an aware server timestamp and day/month period are required')
    local = at.astimezone(ZoneInfo('Asia/Shanghai'))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == 'day':
        from datetime import timedelta
        end = start + timedelta(days=1)
    else:
        start = start.replace(day=1)
        end = start.replace(year=start.year + start.month // 12, month=start.month % 12 + 1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def quota_limits(stage: int) -> dict[str, int]:
    if type(stage) is not int or stage not in (10, 30):
        raise ValueError('stage must be 10 or 30')
    return dict(asr_month_ms=(12 if stage == 10 else 18) * 3_600_000,
                asr_day_ms=3 * 3_600_000,
                retranscribe_month_ms=(2_400 if stage == 10 else 3_600) * 3_600,
                interactive_month_count=20 if stage == 10 else 36,
                summary_month_count=8 if stage == 10 else 12)


def platform_limits(stage: int) -> dict:
    quota_limits(stage)
    amounts = (500, 100, 100, 160, 35, 105) if stage == 10 else (2000, 400, 300, 700, 150, 450)
    total, safety, fixed, asr, filetrans, ai = (amount * MICROYUAN for amount in amounts)
    return dict(total=total, safety=safety, fixed=fixed,
                pools=dict(asr=asr, filetrans=filetrans, ai=ai))


def billing_state(risk: int, budget: int, enabled: bool = True) -> str:
    integer(risk); integer(budget)
    if budget == 0 or risk >= budget:
        return 'blocked100'
    if not enabled:
        return 'paused'
    if risk * 100 >= budget * 80:
        return 'warning80'
    if risk * 100 >= budget * 60:
        return 'warning60'
    return 'normal'


@dataclass(frozen=True)
class Price:
    id: UUID
    provider: str
    model: str
    region: str
    revision: str
    rates: dict
    minimum_microyuan: int
    synthetic: bool
    verified: bool

    def amount(self, quantities: dict[str, int]) -> int:
        """Each configured meter rounds upward, followed by the minimum charge."""
        if not quantities or set(quantities) != set(self.rates) or set(self.rates) - METERS:
            raise BillingBlocked('all configured meter bounds are required')
        total = 0
        for meter, quantity in quantities.items():
            integer(quantity, meter)
            rate = self.rates[meter]
            if set(rate) != {'microyuan', 'units'}:
                raise BillingBlocked('unsupported price rule')
            numerator = integer(rate['microyuan'], 'price')
            denominator = integer(rate['units'], 'meter unit')
            if denominator == 0:
                raise BillingBlocked('meter unit must be positive')
            with localcontext() as context:
                context.prec = 80
                total += int((Decimal(quantity) * numerator / denominator).to_integral_value(rounding=ROUND_CEILING))
        return integer(max(total, integer(self.minimum_microyuan)), 'cost')


@dataclass(frozen=True, kw_only=True)
class ReservationRequest:
    op_id: UUID
    purpose: str
    price_id: UUID
    maximum_units: dict[str, int]
    task_max_microyuan: int
    input_ref: dict
    class_session_id: UUID | None = None
    quota_root_id: UUID | None = None
    schema_version: str = '1'
    plan_id: UUID | None = None
    parent_task_id: UUID | None = None

    def validate(self):
        if self.purpose not in PURPOSES or not isinstance(self.op_id, UUID) or self.op_id.version != 4:
            raise ValueError('invalid reservation identity or purpose')
        integer(self.task_max_microyuan, 'task maximum')
        if not self.maximum_units or set(self.maximum_units) - METERS:
            raise ValueError('bounded supported meters are required')
        for quantity in self.maximum_units.values():
            integer(quantity)
        if self.purpose.startswith('asr_') and self.maximum_units.get('audio_duration_ms', 0) <= 0:
            raise ValueError('ASR requires a bounded positive duration')
        if self.quota_root_id is not None and self.purpose != 'review_summary':
            raise ValueError('only summary children share the root operation quota')
        if self.schema_version not in ('1', 'pr07a-1', 'pr07b-1', 'pr07b-batch-1', 'pr06b-1', 'pr06c-1'):
            raise ValueError('unsupported task schema version')
        if self.schema_version == 'pr07a-1' and (self.purpose != 'ai_question'
                or self.plan_id is None or self.class_session_id is None):
            raise ValueError('question reservations require their generation plan')
        if self.parent_task_id is not None and (self.schema_version not in ('pr06b-1', 'pr06c-1') or
                not isinstance(self.parent_task_id, UUID) or self.parent_task_id.version != 4):
            raise ValueError('only an ASR retry may name a parent task')
        if self.schema_version == 'pr06b-1' and (self.purpose != 'asr_live' or self.plan_id or not self.class_session_id):
            raise ValueError('ASR reservations require a classroom and no generation plan')
        if self.schema_version == 'pr06c-1' and (self.purpose != 'asr_retranscribe'
                or self.plan_id is None or self.class_session_id is None):
            raise ValueError('Filetrans reservations require their frozen audio plan')
        if self.schema_version == '1' and self.plan_id is not None:
            raise ValueError('legacy reservations cannot reference question plans')
        if self.schema_version in ('pr07b-1', 'pr07b-batch-1') and (self.purpose != 'review_summary'
                or self.plan_id is None or self.class_session_id is None):
            raise ValueError('review reservations require their generation plan')


@dataclass(frozen=True, kw_only=True)
class Usage:
    provider_request_id: str
    meter: str
    revision: int
    quantities: dict[str, int] | None
    final: bool
    bill_month: str | None = None
    adjustment_of: UUID | None = None

    def validate(self):
        import re
        if (not isinstance(self.provider_request_id, str) or not 1 <= len(self.provider_request_id) <= 200
                or not isinstance(self.meter, str) or not 1 <= len(self.meter) <= 64
                or type(self.final) is not bool):
            raise ValueError('invalid usage identity')
        integer(self.revision, 'usage revision')
        if self.bill_month is not None and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', self.bill_month):
            raise ValueError('invalid bill month')
        if self.quantities is not None:
            if not self.quantities or set(self.quantities) - METERS:
                raise ValueError('invalid usage meters')
            for quantity in self.quantities.values():
                integer(quantity)
