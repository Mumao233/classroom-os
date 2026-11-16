"""Deterministic, bounded lexical retrieval over authorized immutable revisions."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import datetime
import hashlib
import re
from uuid import UUID

from ..domain.qa import ContextEntry, ContextSelection, QaRejected, Source, input_token_bound


def _terms(text: str) -> frozenset[str]:
    words = set(re.findall(r'[a-z0-9_]+', text.casefold()))
    for run in re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]+', text):
        words.update(run[index:index + 2] for index in range(len(run) - 1))
        if len(run) == 1:
            words.add(run)
    return frozenset(words)


def _windows(text: str, byte_limit: int):
    """Overlapping byte-bounded windows retain offsets into the exact revision."""
    offsets = [0]
    for character in text:
        offsets.append(offsets[-1] + len(character.encode('utf-8')))
    start = 0
    while start < len(text):
        end = bisect_right(offsets, offsets[start] + byte_limit) - 1
        if end <= start:
            return
        yield start, end, text[start:end]
        if end == len(text):
            return
        start = max(start + 1, bisect_left(offsets, offsets[end] - byte_limit // 4))


def _scope_bounds(scope: str, anchor_ms: int, start_ms: int | None, end_ms: int | None):
    if scope not in ('all', 'recent30', 'recent120', 'range'):
        raise QaRejected('UNSUPPORTED_SCOPE')
    if type(anchor_ms) is not int or not 0 <= anchor_ms <= 2**53 - 1:
        raise QaRejected('INVALID_SCOPE')
    if scope == 'range':
        if (type(start_ms) is not int or type(end_ms) is not int
                or not 0 <= start_ms < end_ms <= anchor_ms):
            raise QaRejected('INVALID_SCOPE')
        return start_ms, end_ms
    if start_ms is not None or end_ms is not None:
        raise QaRejected('INVALID_SCOPE')
    if scope == 'all':
        return 0, anchor_ms
    duration = 30_000 if scope == 'recent30' else 120_000
    return max(0, anchor_ms - duration), anchor_ms


def select_context(sources: Iterable[Source], *, question: str, owner_id: UUID,
                   class_session_id: UUID, asked_at: datetime, anchor_ms: int,
                   scope: str = 'all', start_ms: int | None = None,
                   end_ms: int | None = None, input_token_budget: int = 8192,
                   max_output_tokens: int = 1024, max_entries: int = 12) -> ContextSelection:
    if (not isinstance(question, str) or not question.strip() or len(question) > 12_000
            or not isinstance(owner_id, UUID) or not isinstance(class_session_id, UUID)
            or not isinstance(asked_at, datetime)
            or asked_at.tzinfo is None or asked_at.utcoffset() is None):
        raise QaRejected('INVALID_QUESTION')
    if (type(input_token_budget) is not int or not 256 <= input_token_budget <= 262_144
            or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 16_384
            or type(max_entries) is not int or not 1 <= max_entries <= 64):
        raise QaRejected('INVALID_CONTEXT_BUDGET')
    lower, upper = _scope_bounds(scope, anchor_ms, start_ms, end_ms)
    visible: dict[UUID, Source] = {}
    count = total_bytes = 0
    reasons: set[str] = set()
    for source in sources:
        count += 1
        total_bytes += len(source.text.encode('utf-8'))
        if count > 10_000 or total_bytes > 16_000_000:
            raise QaRejected('CONTEXT_INSUFFICIENT')
        if source.owner_id != owner_id or source.class_session_id != class_session_id:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if source.visible_at > asked_at:
            reasons.add('not_visible_at_question')
            continue
        if source.end_ms is not None:
            # Segment text has no word-level alignment. Cutting a segment that
            # straddles the boundary would expose out-of-window classroom words.
            if source.start_ms < lower or source.end_ms > upper:
                reasons.add('outside_time_window')
                if source.start_ms < upper and source.end_ms > lower:
                    reasons.add('unaligned_boundary_segment')
                continue
        elif scope != 'all':
            reasons.add('source_has_no_classroom_time')
            continue
        if not source.text.strip():
            reasons.add('empty_source')
            continue
        prior = visible.get(source.source_id)
        if prior is not None and prior.revision_id == source.revision_id and prior != source:
            raise QaRejected('SOURCE_UNAVAILABLE', 404)
        if prior is None or (source.visible_at, str(source.revision_id)) > (
                prior.visible_at, str(prior.revision_id)):
            visible[source.source_id] = source
    if not count:
        raise QaRejected('CONTEXT_EMPTY')
    if not visible:
        raise QaRejected('CONTEXT_INSUFFICIENT')
    base_tokens = input_token_bound(question, ())
    # Leave room for per-excerpt JSON framing. Token counts use UTF-8 bytes,
    # including both system/user messages, rather than a language-specific ratio.
    available = input_token_budget - base_tokens - 200
    if available < 32:
        raise QaRejected('CONTEXT_INSUFFICIENT')
    window_bytes = min(1600, available)
    query_terms = _terms(question)
    candidates = []
    distinct = set()
    for source in sorted(visible.values(), key=lambda item: str(item.source_id)):
        for start, end, excerpt in _windows(source.text, window_bytes):
            normalized = ' '.join(excerpt.split()).casefold()
            digest = hashlib.sha256(normalized.encode('utf-8')).digest()
            if digest in distinct:
                reasons.add('duplicate_excerpt')
                continue
            distinct.add(digest)
            overlap = len(query_terms.intersection(_terms(excerpt)))
            candidates.append((overlap, source.end_ms or 0, str(source.source_id),
                               start, end, excerpt, source))
    candidates.sort(key=lambda value: (-value[0], -value[1], value[2], value[3]))
    entries: tuple[ContextEntry, ...] = ()
    selected_spans: dict[UUID, list[tuple[int, int]]] = {}
    for score, _, _, start, end, excerpt, source in candidates:
        spans = selected_spans.get(source.source_id, [])
        if any(start < existing_end and end > existing_start
               for existing_start, existing_end in spans):
            continue
        entry = ContextEntry(alias=f'S{len(entries) + 1}', source=source, excerpt=excerpt,
                             excerpt_start=start, excerpt_end=end,
                             token_bound=len(excerpt.encode('utf-8')))
        proposed = (*entries, entry)
        if input_token_bound(question, proposed) > input_token_budget:
            reasons.add('input_budget')
            continue
        if len(entries) >= max_entries:
            reasons.add('entry_limit')
            break
        entries = proposed
        selected_spans.setdefault(source.source_id, []).append((start, end))
    if not entries:
        raise QaRejected('CONTEXT_INSUFFICIENT')
    complete = 'unaligned_boundary_segment' not in reasons and all(sum(end - start for start, end in selected_spans.get(identity, []))
                   == len(source.text) for identity, source in visible.items())
    if not complete:
        reasons.add('partial_source_coverage')
    if not candidates[0][0]:
        reasons.add('no_lexical_match')
    return ContextSelection(entries=entries, token_bound=input_token_bound(question, entries),
                            scope_complete=complete, reasons=tuple(sorted(reasons)))
