"""Deterministic, complete source-unit selection for bounded review generation."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from ..domain.qa import ContextEntry, ContextSelection, QaRejected, Source, input_token_bound
from ..domain.review import REVIEW_PURPOSES, review_batch_question
from .qa_context import _scope_bounds


def select_review_context(sources: Iterable[Source], *, question: str, owner_id: UUID,
                          class_session_id: UUID, asked_at: datetime, anchor_ms: int,
                          scope: str = 'all', start_ms: int | None = None,
                          end_ms: int | None = None, input_token_budget: int = 8192,
                          max_output_tokens: int = 1024, max_entries: int = 12,
                          purpose: str = 'review_summary') -> ContextSelection:
    """Retain the original bounded single-call selection for existing callers."""
    if type(max_entries) is not int or not 1 <= max_entries <= 12:
        raise QaRejected('INVALID_CONTEXT_BUDGET')
    selected = _review_selection(sources, question=question, owner_id=owner_id,
        class_session_id=class_session_id, asked_at=asked_at, anchor_ms=anchor_ms,
        scope=scope, start_ms=start_ms, end_ms=end_ms, input_token_budget=input_token_budget,
        max_output_tokens=max_output_tokens, max_entries=max_entries, purpose=purpose)
    if selected.token_bound > input_token_budget:
        raise QaRejected('CONTEXT_BUDGET_EXCEEDED', 422)
    return selected


def plan_review_context(sources: Iterable[Source], *, question: str, owner_id: UUID,
                        class_session_id: UUID, asked_at: datetime, anchor_ms: int,
                        scope: str = 'all', start_ms: int | None = None,
                        end_ms: int | None = None, input_token_budget: int = 8192,
                        max_output_tokens: int = 1024, max_entries: int = 512,
                        purpose: str = 'review_summary') -> ContextSelection:
    """Freeze the complete scope and require a bounded partition before execution."""
    selected = _review_selection(sources, question=question, owner_id=owner_id,
        class_session_id=class_session_id, asked_at=asked_at, anchor_ms=anchor_ms,
        scope=scope, start_ms=start_ms, end_ms=end_ms, input_token_budget=input_token_budget,
        max_output_tokens=max_output_tokens, max_entries=max_entries, purpose=purpose)
    partition_review_context(selected, question=question, input_token_budget=input_token_budget,
                             max_output_tokens=max_output_tokens, purpose=purpose)
    return selected


def _review_selection(sources: Iterable[Source], *, question: str, owner_id: UUID,
                      class_session_id: UUID, asked_at: datetime, anchor_ms: int,
                      scope: str, start_ms: int | None, end_ms: int | None,
                      input_token_budget: int, max_output_tokens: int,
                      max_entries: int, purpose: str) -> ContextSelection:
    """Select authorized complete units with stable aliases and exact revisions.

    Callers supply server-authorized immutable sources. An explicit time range
    excludes wholly outside units. A partially overlapping unit cannot be split
    without word-level timing, so that scope must be changed before generation.
    """
    if (not isinstance(question, str) or not question.strip() or len(question) > 12_000
            or not isinstance(owner_id, UUID) or not isinstance(class_session_id, UUID)
            or not isinstance(asked_at, datetime) or asked_at.tzinfo is None
            or asked_at.utcoffset() is None or not isinstance(purpose, str)
            or purpose not in REVIEW_PURPOSES):
        raise QaRejected('INVALID_REVIEW_REQUEST')
    if (type(input_token_budget) is not int or not 256 <= input_token_budget <= 262_144
            or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16_384
            or type(max_entries) is not int or not 1 <= max_entries <= 512):
        raise QaRejected('INVALID_CONTEXT_BUDGET')
    try:
        question.encode('utf-8')
    except UnicodeError:
        raise QaRejected('INVALID_REVIEW_REQUEST') from None
    lower, upper = _scope_bounds(scope, anchor_ms, start_ms, end_ms)
    visible: dict[UUID, Source] = {}
    count = total_bytes = 0
    reasons = set()
    for source in sources:
        if not isinstance(source, Source):
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if source.owner_id != owner_id or source.class_session_id != class_session_id:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        count += 1
        total_bytes += len(source.text.encode('utf-8'))
        if count > 10_000 or total_bytes > 16_000_000:
            raise QaRejected('CONTEXT_LIMIT', 422)
        if source.visible_at > asked_at:
            reasons.add('not_visible_at_question')
            continue
        prior = visible.get(source.source_id)
        if prior is not None and prior.revision_id == source.revision_id and prior != source:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if prior is None or (source.visible_at, str(source.revision_id)) > (
                prior.visible_at, str(prior.revision_id)):
            visible[source.source_id] = source
    if not count:
        raise QaRejected('CONTEXT_EMPTY')
    selected = []
    for source in visible.values():
        if source.start_ms is None:
            if scope != 'all':
                raise QaRejected('SOURCE_TIME_UNAVAILABLE', 409)
        elif source.start_ms < lower or source.end_ms > upper:
            if source.start_ms < upper and source.end_ms > lower:
                raise QaRejected('SOURCE_TIME_UNALIGNED', 409)
            reasons.add('outside_time_window')
            continue
        if not source.text.strip():
            raise QaRejected('CONTEXT_INSUFFICIENT', 409)
        selected.append(source)
    if not selected:
        raise QaRejected('CONTEXT_INSUFFICIENT')
    if len(selected) > max_entries:
        raise QaRejected('CONTEXT_LIMIT', 422)
    selected.sort(key=lambda source: (
        source.start_ms is None, source.start_ms or 0, str(source.source_id)))
    entries = tuple(ContextEntry(alias=f'S{index}', source=source, excerpt=source.text,
                    excerpt_start=0, excerpt_end=len(source.text),
                    token_bound=len(source.text.encode('utf-8')))
                    for index, source in enumerate(selected, 1))
    token_bound = input_token_bound(question, entries, purpose=purpose)
    return ContextSelection(entries=entries, token_bound=token_bound,
                            scope_complete=True, reasons=tuple(sorted(reasons)))


def partition_review_context(selection: ContextSelection, *, question: str,
                             input_token_budget: int, max_output_tokens: int,
                             purpose: str = 'review_summary') -> tuple[ContextSelection, ...]:
    """Partition complete units while retaining the global snapshot's S aliases."""
    if (type(input_token_budget) is not int or not 256 <= input_token_budget <= 262_144
            or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16_384
            or not isinstance(purpose, str) or purpose not in REVIEW_PURPOSES):
        raise QaRejected('INVALID_CONTEXT_BUDGET')
    if not isinstance(selection, ContextSelection) or not 1 <= len(selection.entries) <= 512:
        raise QaRejected('CONTEXT_LIMIT', 422)
    try:
        conservative_question = review_batch_question(question, 31, 32)
    except (ValueError, UnicodeError):
        raise QaRejected('INVALID_REVIEW_REQUEST') from None
    groups = []
    current = ()
    for entry in selection.entries:
        if (entry.excerpt_start != 0 or entry.excerpt_end != len(entry.source.text)
                or entry.excerpt != entry.source.text):
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if input_token_bound(conservative_question, (entry,), purpose=purpose) > input_token_budget:
            raise QaRejected('CONTEXT_BUDGET_EXCEEDED', 422)
        proposed = (*current, entry)
        if len(proposed) > 64 or input_token_bound(
                conservative_question, proposed, purpose=purpose) > input_token_budget:
            groups.append(current)
            current = (entry,)
        else:
            current = proposed
    groups.append(current)
    if len(groups) > 32:
        raise QaRejected('CONTEXT_LIMIT', 422)
    result = []
    for index, entries in enumerate(groups):
        batch_question = review_batch_question(question, index, len(groups))
        bound = input_token_bound(batch_question, entries, purpose=purpose)
        if bound > input_token_budget:
            raise QaRejected('CONTEXT_BUDGET_EXCEEDED', 422)
        result.append(ContextSelection(entries=entries, token_bound=bound,
                      scope_complete=selection.scope_complete, reasons=selection.reasons))
    return tuple(result)
