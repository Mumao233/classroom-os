import { describe, expect, it } from 'vitest';
import type { components } from '../src/generated/openapi';
import { assessProcessing, buildTimeline, columnWidths, createDraftStore, defaultLayout, formatClock, formatYuan, layoutMode, MIN_WIDTH, moveSeparator, parseLayout, preparationQuotaLines, SEPARATOR_PX, type LiveSegmentInput } from '../src/domain/classroom';

type S = components['schemas'];

describe('classroom preparation quota', () => {
  const period = { period_start: '2026-11-01T00:00:00Z', period_end: '2026-12-01T00:00:00Z', reserved: 0, submitted: 0, settled: 0, unknown: 0 };
  const buckets: S['QuotaBucket'][] = [
    { ...period, kind: 'asr_month_ms', limit: 120000, remaining: 120000 },
    { ...period, kind: 'interactive_month_count', limit: 5, remaining: 5 },
  ];

  it('shows balances alone when only retranscription and summaries might be enabled', () => {
    expect(preparationQuotaLines({ buckets, platform_billing_state: 'normal', asr_enabled: true, ai_enabled: true }))
      .toEqual(['本月转写额度剩余约 2 分钟', '本月问答与解释额度剩余 5 次']);
  });

  it('reports a group pause even when its quota balance is positive', () => {
    expect(preparationQuotaLines({ buckets, platform_billing_state: 'normal', asr_enabled: false, ai_enabled: true }))
      .toEqual(['本月转写额度剩余约 2 分钟', '本月问答与解释额度剩余 5 次', '新的转写请求已暂停']);
    expect(preparationQuotaLines({ buckets: [], platform_billing_state: 'normal', asr_enabled: true, ai_enabled: false }))
      .toEqual(['暂无本月转写额度信息', '暂无本月问答与解释额度信息', '新的 AI 请求已暂停']);
  });
});

const CLASS = 'c1000000-0000-4000-8000-000000000001';
const OTHER = 'c1000000-0000-4000-8000-000000000002';
const live = (over: Partial<LiveSegmentInput>): LiveSegmentInput => ({ classroomId: CLASS, streamId: 's', segmentId: 'seg-1', sequence: 1, revision: 1, text: 'x', status: 'final', startMs: 0, endMs: 1000, ...over });
const server = (over: Partial<S['TranscriptSegment']>): S['TranscriptSegment'] => ({
  id: 'seg-1', revision: 1, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z', class_session_id: CLASS, transcript_version_id: 'v', stream_id: null,
  segment_revision_id: 'r', kind: 'machine', origin: 'live_asr', status: 'final', text: 'server', start_ms: 0, end_ms: 1000, timestamp_source: 'capture_mapping', provider_request_id: null, source_chunk_ids: [], adopted_revision_id: null, ...over,
});

describe('transcript timeline (UI-CLASS-07)', () => {
  it('a late final for an earlier time is inserted in time order, not appended', () => {
    const entries = buildTimeline(CLASS, [], [
      live({ segmentId: 'a', sequence: 1, startMs: 0, text: 'first' }),
      live({ segmentId: 'c', sequence: 3, startMs: 12_000, text: 'third' }),
      live({ segmentId: 'b', sequence: 9, startMs: 6_000, text: 'late second' }),
    ]);
    expect(entries.map(entry => entry.text)).toEqual(['first', 'late second', 'third']);
  });

  it('duplicate or older deliveries never replace a newer final; a partial disappears once its final exists', () => {
    const entries = buildTimeline(CLASS, [server({ id: 'a', revision: 3, text: 'newest' })], [
      live({ segmentId: 'a', revision: 2, text: 'older live' }),
      live({ segmentId: 'a', revision: 3, text: 'duplicate' }),
      live({ segmentId: 'a', status: 'partial', revision: 9, text: 'partial after final', endMs: null }),
      live({ segmentId: 'b', status: 'partial', startMs: 5_000, endMs: null, text: 'still partial' }),
    ]);
    expect(entries.map(entry => [entry.segmentId, entry.status, entry.text])).toEqual([['a', 'final', 'newest'], ['b', 'partial', 'still partial']]);
  });

  it('a higher live revision wins over the stored server revision, with its own revision ID', () => {
    const entry = buildTimeline(CLASS, [server({ revision: 1, segment_revision_id: 'old' })], [live({ revision: 2, text: 'revised', segmentRevisionId: 'new' })])[0]!;
    expect(entry).toMatchObject({ text: 'revised', segmentRevisionId: 'new' });
  });

  it('other classrooms, personal revisions and unknown times are handled without invention', () => {
    const entries = buildTimeline(CLASS, [server({ id: 'p', kind: 'personal', text: 'mine' }), server({ id: 'o', class_session_id: OTHER, text: 'other class' })], [
      live({ segmentId: 'n', startMs: null, endMs: null, text: 'no time' }),
      live({ segmentId: 't', startMs: 3_000, text: 'timed' }),
      live({ segmentId: 'x', classroomId: OTHER, text: 'other live' }),
    ]);
    expect(entries.map(entry => entry.text)).toEqual(['timed', 'no time']);
    expect(formatClock(null)).toBe('--:--');
    expect(formatClock(3_725_000)).toBe('1:02:05');
  });
});

describe('classroom layout (UI-CLASS-02/12, CF-14)', () => {
  it('uses container width breakpoints with minimum readable widths', () => {
    expect(layoutMode(1159)).toBe('two');
    expect(layoutMode(1160)).toBe('three');
    // PRD p18 initial value is 720 px, but the auxiliary column hosts either tab (max 320 px):
    // 440 + 320 + separator = 772 px. Minimums win (CF-14).
    expect(layoutMode(772)).toBe('two');
    expect(layoutMode(771)).toBe('one');
    expect(layoutMode(720)).toBe('one');
    expect(layoutMode(320)).toBe('one');
  });

  it('initial ratios never violate a minimum width and the separators are counted', () => {
    for (const width of [1160, 1280, 1440, 1920]) {
      const widths = columnWidths(width, defaultLayout());
      expect(widths.transcript).toBeGreaterThanOrEqual(MIN_WIDTH.transcript);
      expect(widths.explanation).toBeGreaterThanOrEqual(MIN_WIDTH.explanation);
      expect(widths.qa).toBeGreaterThanOrEqual(MIN_WIDTH.qa);
      expect(widths.transcript + widths.explanation + widths.qa + 2 * SEPARATOR_PX).toBeLessThanOrEqual(width + 2);
    }
  });

  it('a separator stops at either minimum; collapsing a panel gives its space to the others', () => {
    const prefs = defaultLayout();
    const wide = moveSeparator(1160, prefs, 'transcript', 'explanation', 5000);
    expect(columnWidths(1160, wide).explanation).toBe(MIN_WIDTH.explanation);
    const narrow = moveSeparator(1160, prefs, 'transcript', 'explanation', -5000);
    expect(columnWidths(1160, narrow).transcript).toBe(MIN_WIDTH.transcript);
    const collapsed = { ...prefs, collapsed: { explanation: true, qa: false } };
    const widths = columnWidths(1160, collapsed);
    expect(widths.explanation).toBe(0);
    expect(widths.transcript + widths.qa + SEPARATOR_PX).toBeLessThanOrEqual(1162);
  });

  it('stored layout is parsed defensively', () => {
    expect(parseLayout('not json')).toEqual(defaultLayout());
    expect(parseLayout(JSON.stringify({ ratios: { transcript: 5, explanation: 0, qa: -1 } })).ratios).toEqual(defaultLayout().ratios);
    expect(parseLayout(JSON.stringify({ ratios: { transcript: 0.5, explanation: 0.2, qa: 0.3 }, collapsed: { qa: true } }))).toEqual({ ratios: { transcript: 0.5, explanation: 0.2, qa: 0.3 }, collapsed: { explanation: false, qa: true } });
  });
});

describe('processing assessment (UI-CLASS-10/11, FE-05/09)', () => {
  const session = (over: Partial<S['ClassSession']> = {}, flows: Partial<S['FlowStates']> = {}): S['ClassSession'] => ({
    id: CLASS, revision: 3, created_at: '2026-10-28T06:00:00Z', updated_at: '2026-10-28T08:00:00Z', course_id: 'c', title: 't', language: 'auto', started_at: '2026-10-28T06:00:00Z',
    capture_ended_at: '2026-10-28T07:30:00Z', audio_expires_at: '2026-11-04T07:30:00Z', adopted_transcript_version_id: null, effective_capture_ms: 5_400_000, known_gap_count: 0, unknown_gap_count: 0, deleted_at: null,
    flows: { capture: 'ended', upload: 'durable', asr: 'succeeded', ai: 'idle', sync: 'synced', ...flows }, ...over,
  });
  const manifest = (missing: S['AudioManifest']['missing_ranges'] = []): S['AudioManifest'] => ({ class_session_id: CLASS, capture_epoch_id: 'e', sealed: true, expected_last_seq: 10, durable_ranges: [{ first_seq: 0, last_seq: 10 }], missing_ranges: missing, unknown_provider_ranges: [], audio_expires_at: null });
  const task = (state: S['Task']['state']): S['Task'] => ({ id: `t-${state}`, revision: 1, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z', class_session_id: CLASS, kind: 'review_summary', state, attempt_id: null, parent_task_id: null, completed_units: 0, total_units: 1, error_code: null, result_id: null, source_snapshot_id: null, provider_outcome: 'unknown', maximum_cost_microyuan: '1', settled_cost_microyuan: null, may_have_charged: state === 'unknown' });

  it('everything finished is complete and safe to leave', () => {
    const result = assessProcessing({ session: session(), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(result).toMatchObject({ complete: true, canLeaveSafely: true, leaveWarning: null, missing: [] });
  });

  it('local-only audio blocks a safe leave and is never folded into a complete state', () => {
    const result = assessProcessing({ session: session(), manifests: [manifest()], localStopPending: false, localPendingChunks: 2, tasks: [] });
    expect(result.complete).toBe(false);
    expect(result.canLeaveSafely).toBe(false);
    expect(result.leaveWarning).toContain('2 个音频分片');
    expect(result.items.find(item => item.key === 'local')?.tone).toBe('danger');
  });

  it('an unconfirmed stop, missing chunks or unfinished ASR each make the material partial', () => {
    expect(assessProcessing({ session: session({ capture_ended_at: null }, { capture: 'ended' }), manifests: null, localStopPending: true, localPendingChunks: 0, tasks: [] }).missing).toContain('停止范围尚未确认');
    expect(assessProcessing({ session: session(), manifests: [manifest([{ first_seq: 4, last_seq: 6 }])], localStopPending: false, localPendingChunks: 0, tasks: [] }).missing).toContain('原音缺少 3 个分片');
    const draining = assessProcessing({ session: session({}, { asr: 'draining' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(draining.complete).toBe(false);
    expect(draining.canLeaveSafely).toBe(true);
    expect(draining.missing).toContain('转写仍在处理');
  });

  it('unfinished, failed or running AI work keeps the class from being complete (review r0)', () => {
    for (const state of ['queued', 'running', 'failed', 'cancel_requested'] as const) {
      expect(assessProcessing({ session: session(), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [task(state)] }).complete).toBe(false);
    }
    expect(assessProcessing({ session: session({}, { ai: 'running' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] }).complete).toBe(false);
    expect(assessProcessing({ session: session({}, { ai: 'succeeded' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [task('succeeded')] }).complete).toBe(true);
  });

  it('upload failure with an ended capture is not complete; unknown tasks are listed for reconciliation', () => {
    const result = assessProcessing({ session: session({}, { upload: 'failed' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [task('unknown'), task('succeeded')] });
    expect(result.complete).toBe(false);
    expect(result.unknownTasks.map(item => item.id)).toEqual(['t-unknown']);
  });

  it('gaps are counted per capture epoch; an unread or empty list never counts as complete (0.3.0)', () => {
    const retired = { ...manifest([{ first_seq: 2, last_seq: 3 }]), capture_epoch_id: 'e0' };
    const result = assessProcessing({ session: session(), manifests: [retired, manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(result.complete).toBe(false);
    expect(result.missing).toContain('原音缺少 2 个分片');
    expect(result.items.find(item => item.key === 'manifest')?.detail).toContain('第 1 段采集缺分片 2–3（2 个）');
    expect(assessProcessing({ session: session(), manifests: null, localStopPending: false, localPendingChunks: 0, tasks: [] }).complete).toBe(false);
    expect(assessProcessing({ session: session(), manifests: [], localStopPending: false, localPendingChunks: 0, tasks: [] }).missing).toContain('服务端没有原音记录');
    expect(assessProcessing({ session: session(), manifests: [manifest(), manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] }).complete).toBe(true);
  });

  it('the audio verdict is class-wide: seal state is separate, upload must be durable, intervals stay per epoch (review r0)', () => {
    const unsealed = { ...manifest(), sealed: false, capture_epoch_id: 'e0' };
    const durable = assessProcessing({ session: session(), manifests: [unsealed, manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(durable.complete).toBe(true);
    expect(durable.items.find(item => item.key === 'manifest')?.tone).toBe('success');
    const pending = assessProcessing({ session: session({}, { upload: 'pending' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(pending.items.find(item => item.key === 'manifest')).toBeUndefined();
    expect(pending.items.find(item => item.key === 'upload')?.tone).toBe('warning');
    const disjoint = assessProcessing({ session: session(), manifests: [manifest([{ first_seq: 2, last_seq: 3 }, { first_seq: 8, last_seq: 9 }])], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(disjoint.items.find(item => item.key === 'manifest')?.detail).toContain('缺分片 2–3、8–9（4 个）');
  });

  it('different retention deadlines name the earliest as partial (review r1)', () => {
    const early = { ...manifest(), capture_epoch_id: 'e0', audio_expires_at: '2026-11-02T08:00:00+08:00' };
    const late = { ...manifest(), audio_expires_at: '2026-11-03T00:00:00Z' };
    const detail = assessProcessing({ session: session(), manifests: [late, early], localStopPending: false, localPendingChunks: 0, tasks: [] }).items.find(item => item.key === 'manifest')?.detail;
    expect(detail).toContain('已知最早到期的原音保留至 2026-11-02');
    const shared = assessProcessing({ session: session(), manifests: [early, early, late], localStopPending: false, localPendingChunks: 0, tasks: [] }).items.find(item => item.key === 'manifest')?.detail;
    expect(shared).toContain('已知最早到期的原音保留至 2026-11-02');
    expect(shared).not.toContain('其余');
    const partlyUnknown = assessProcessing({ session: session(), manifests: [late, manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] }).items.find(item => item.key === 'manifest')?.detail;
    expect(partlyUnknown).toContain('部分段落的保留期限未知');
  });

  it('unavailable audio is not told to refresh; a pending read is (review r2)', () => {
    const gone = assessProcessing({ session: session(), manifests: null, manifestsUnavailable: true, localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(gone.complete).toBe(false);
    expect(gone.items.find(item => item.key === 'manifest')?.title).toBe('部分原音已过保留期限或已删除');
    expect(gone.missing).toContain('部分原音已不可读取');
    expect(assessProcessing({ session: session(), manifests: null, localStopPending: false, localPendingChunks: 0, tasks: [] }).items.find(item => item.key === 'manifest')?.detail).toContain('稍后刷新');
  });

  it('a class-wide pending upload keeps the material partial although the final epoch is complete (review r3)', () => {
    const result = assessProcessing({ session: session({}, { upload: 'pending' }), manifests: [manifest()], localStopPending: false, localPendingChunks: 0, tasks: [] });
    expect(result.complete).toBe(false);
    expect(result.missing).toContain('部分原音尚未持久确认');
    expect(result.items.find(item => item.key === 'upload')?.tone).toBe('warning');
  });
});

describe('drafts and money formatting', () => {
  it('drafts are isolated per classroom and cleared with the account store', () => {
    const drafts = createDraftStore();
    let notified = 0;
    drafts.subscribe(() => notified++);
    drafts.set(CLASS, 'question', '为什么？');
    expect(drafts.get(CLASS, 'question')).toBe('为什么？');
    expect(drafts.get(OTHER, 'question')).toBe('');
    drafts.set(CLASS, 'question', '为什么？');
    expect(notified).toBe(1);
    drafts.clear();
    expect(drafts.get(CLASS, 'question')).toBe('');
  });

  it('microyuan strings format without floating-point rounding', () => {
    expect(formatYuan('120000')).toBe('¥0.12');
    // Sub-cent amounts are shown exactly, never rounded down (review r1).
    expect(formatYuan('12345678')).toBe('¥12.345678');
    expect(formatYuan('9000')).toBe('¥0.009');
    expect(formatYuan('1000000')).toBe('¥1.00');
    expect(formatYuan('0')).toBe('¥0.00');
    expect(formatYuan('1.5')).toBe('—');
    expect(formatYuan(null)).toBe('—');
  });
});
