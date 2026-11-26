import type { components } from '../generated/openapi';
import { isClassComplete } from './flows';

/** Pure PR-10a classroom logic: no DOM, network, storage or runtime imports. */
type S = components['schemas'];

export function preparationQuotaLines(quota: S['QuotaView']): string[] {
  const asr = quota.buckets.find(bucket => bucket.kind === 'asr_month_ms');
  const interaction = quota.buckets.find(bucket => bucket.kind === 'interactive_month_count');
  const lines = [
    asr ? `本月转写额度剩余约 ${Math.floor(asr.remaining / 60000)} 分钟` : '暂无本月转写额度信息',
    interaction ? `本月问答与解释额度剩余 ${interaction.remaining} 次` : '暂无本月问答与解释额度信息',
  ];
  // A disabled group covers every member. An enabled group can still have paused services.
  if (!quota.asr_enabled) lines.push('新的转写请求已暂停');
  if (!quota.ai_enabled) lines.push('新的 AI 请求已暂停');
  return lines;
}

// ---------------------------------------------------------------- transcript timeline

/** One live segment as projected from transcript events (shape owned by the capture runtime). */
export type LiveSegmentInput = { classroomId: string; streamId: string; segmentId: string; sequence: number; revision: number; text: string; status: 'partial' | 'final'; startMs: number | null; endMs: number | null; segmentRevisionId?: string | null };
export type TimelineEntry = { key: string; segmentId: string; status: 'partial' | 'final'; text: string; startMs: number | null; endMs: number | null; revision: number; sequence: number | null; segmentRevisionId: string | null };

/**
 * Durable server segments and live events merge by segment ID; the higher revision wins, and an
 * older or duplicate delivery never replaces a newer one. A partial is shown only while no final
 * exists for the same segment. Entries are ordered by their original time, so a late or
 * reconnect-replayed final is inserted where it belongs instead of being appended.
 */
export function buildTimeline(classroomId: string, server: readonly S['TranscriptSegment'][], live: readonly LiveSegmentInput[]): TimelineEntry[] {
  const finals = new Map<string, TimelineEntry>();
  for (const item of server) {
    if (item.class_session_id !== classroomId || item.kind !== 'machine' || item.status !== 'final') continue;
    const previous = finals.get(item.id);
    if (!previous || item.revision > previous.revision) finals.set(item.id, { key: `final:${item.id}`, segmentId: item.id, status: 'final', text: item.text, startMs: item.start_ms, endMs: item.end_ms, revision: item.revision, sequence: null, segmentRevisionId: item.segment_revision_id });
  }
  for (const item of live) {
    if (item.classroomId !== classroomId || item.status !== 'final') continue;
    const previous = finals.get(item.segmentId);
    if (!previous || item.revision > previous.revision) finals.set(item.segmentId, { key: `final:${item.segmentId}`, segmentId: item.segmentId, status: 'final', text: item.text, startMs: item.startMs, endMs: item.endMs, revision: item.revision, sequence: item.sequence, segmentRevisionId: item.segmentRevisionId ?? null });
    else if (previous.sequence === null) previous.sequence = item.sequence;
  }
  const partials = live.filter(item => item.classroomId === classroomId && item.status === 'partial' && !finals.has(item.segmentId))
    .map(item => ({ key: `partial:${item.segmentId}`, segmentId: item.segmentId, status: 'partial' as const, text: item.text, startMs: item.startMs, endMs: null, revision: item.revision, sequence: item.sequence, segmentRevisionId: null }));
  return [...finals.values(), ...partials].sort(compareEntries);
}

function compareEntries(a: TimelineEntry, b: TimelineEntry): number {
  // Unknown time positions are never given a fabricated time; they stay after timed entries.
  if (a.startMs === null || b.startMs === null) {
    if (a.startMs !== b.startMs) return a.startMs === null ? 1 : -1;
  } else if (a.startMs !== b.startMs) return a.startMs - b.startMs;
  if (a.status !== b.status) return a.status === 'final' ? -1 : 1;
  return (a.sequence ?? Number.MAX_SAFE_INTEGER) - (b.sequence ?? Number.MAX_SAFE_INTEGER) || a.segmentId.localeCompare(b.segmentId);
}

export function formatClock(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return '--:--';
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

// ---------------------------------------------------------------- layout

export type PanelKey = 'transcript' | 'explanation' | 'qa';
export type LayoutMode = 'three' | 'two' | 'one';
/** PRD p18 initial minimum readable widths (CSS px) and the separator width counted in the container. */
export const MIN_WIDTH: Record<PanelKey, number> = { transcript: 440, explanation: 280, qa: 320 };
export const SEPARATOR_PX = 12;
export const THREE_COLUMN_MIN = 1160;
export const TWO_COLUMN_MIN = 720;
export const DEFAULT_RATIOS: Record<PanelKey, number> = { transcript: 0.44, explanation: 0.25, qa: 0.31 };
export type LayoutPrefs = { ratios: Record<PanelKey, number>; collapsed: { explanation: boolean; qa: boolean } };
export const defaultLayout = (): LayoutPrefs => ({ ratios: { ...DEFAULT_RATIOS }, collapsed: { explanation: false, qa: false } });

/** Breakpoints use the classroom container width (not the viewport) and must fit minimum widths. */
export function layoutMode(containerWidth: number): LayoutMode {
  const threeNeeds = MIN_WIDTH.transcript + MIN_WIDTH.explanation + MIN_WIDTH.qa + 2 * SEPARATOR_PX;
  if (containerWidth >= Math.max(THREE_COLUMN_MIN, threeNeeds)) return 'three';
  // The auxiliary column hosts either tab, so it needs the larger of the two minimums.
  if (containerWidth >= Math.max(TWO_COLUMN_MIN, MIN_WIDTH.transcript + Math.max(MIN_WIDTH.explanation, MIN_WIDTH.qa) + SEPARATOR_PX)) return 'two';
  return 'one';
}

/** Pixel widths for the visible three-column panels; ratios are initial values, minimums always win. */
export function columnWidths(containerWidth: number, prefs: LayoutPrefs): Record<PanelKey, number> {
  const visible = visiblePanels(prefs);
  const separators = (visible.length - 1) * SEPARATOR_PX;
  const free = Math.max(0, containerWidth - separators);
  const total = visible.reduce((sum, key) => sum + prefs.ratios[key], 0) || 1;
  const widths: Record<PanelKey, number> = { transcript: 0, explanation: 0, qa: 0 };
  for (const key of visible) widths[key] = Math.round((prefs.ratios[key] / total) * free);
  // Raise any panel below its minimum by taking width from panels that have room to spare.
  for (const key of visible) {
    let deficit = MIN_WIDTH[key] - widths[key];
    for (const donor of visible) {
      if (deficit <= 0) break;
      if (donor === key) continue;
      const spare = widths[donor] - MIN_WIDTH[donor];
      const give = Math.max(0, Math.min(spare, deficit));
      widths[donor] -= give; widths[key] += give; deficit -= give;
    }
  }
  return widths;
}

export function visiblePanels(prefs: LayoutPrefs): PanelKey[] {
  return (['transcript', 'explanation', 'qa'] as const).filter(key => key === 'transcript' || !prefs.collapsed[key]);
}

/**
 * Move the separator between two adjacent visible panels by deltaPx (keyboard or pointer). The
 * result keeps both panels at or above their minimum width and returns normalised ratios.
 */
export function moveSeparator(containerWidth: number, prefs: LayoutPrefs, left: PanelKey, right: PanelKey, deltaPx: number): LayoutPrefs {
  const widths = columnWidths(containerWidth, prefs);
  const pair = widths[left] + widths[right];
  const nextLeft = Math.min(pair - MIN_WIDTH[right], Math.max(MIN_WIDTH[left], widths[left] + deltaPx));
  const next = { ...widths, [left]: nextLeft, [right]: pair - nextLeft };
  const visible = visiblePanels(prefs);
  const sum = visible.reduce((total, key) => total + next[key], 0) || 1;
  const ratios = { ...prefs.ratios };
  for (const key of visible) ratios[key] = next[key] / sum;
  return { ...prefs, ratios };
}

/** Restores stored layout values defensively: invalid or tampered values fall back to defaults. */
export function parseLayout(raw: string | null): LayoutPrefs {
  const fallback = defaultLayout();
  if (!raw) return fallback;
  try {
    const value = JSON.parse(raw) as Partial<LayoutPrefs>;
    const ratios = value.ratios ?? fallback.ratios;
    const valid = (['transcript', 'explanation', 'qa'] as const).every(key => typeof ratios[key] === 'number' && ratios[key] > 0.05 && ratios[key] < 0.95);
    return {
      ratios: valid ? { transcript: ratios.transcript, explanation: ratios.explanation, qa: ratios.qa } : fallback.ratios,
      collapsed: { explanation: value.collapsed?.explanation === true, qa: value.collapsed?.qa === true },
    };
  } catch { return fallback; }
}

// ---------------------------------------------------------------- processing page

export type ProcessingTone = 'success' | 'info' | 'warning' | 'danger';
export type ProcessingItem = { key: string; tone: ProcessingTone; title: string; detail: string };
export type ProcessingInput = {
  session: S['ClassSession'];
  /** Every capture epoch's manifest, read to the last page; null while not (fully) read or after a failure. */
  manifests: readonly S['AudioManifest'][] | null;
  /** The read failed because some epoch's audio expired or was deleted (GONE): no refresh brings it back. */
  manifestsUnavailable?: boolean;
  /** Stop confirmed by the server (capture_ended_at set) versus only stopped in this page. */
  localStopPending: boolean;
  /** Chunks this device still holds that the server has not durably acknowledged. */
  localPendingChunks: number;
  tasks: readonly S['Task'][];
};
export type ProcessingAssessment = {
  items: ProcessingItem[];
  /** Every flow finished independently; never inferred from capture alone. */
  complete: boolean;
  /** Leaving is safe only for work the server has already accepted. */
  canLeaveSafely: boolean;
  leaveWarning: string | null;
  /** Known gaps that make a summary partial, shown before any request is prepared. */
  missing: string[];
  unknownTasks: S['Task'][];
};

const countRanges = (ranges: ReadonlyArray<{ first_seq: number; last_seq: number }>) => ranges.reduce((n, range) => n + range.last_seq - range.first_seq + 1, 0);

export function assessProcessing(input: ProcessingInput): ProcessingAssessment {
  const { session, manifests } = input;
  const items: ProcessingItem[] = [];
  const missing: string[] = [];
  const stopped = session.capture_ended_at !== null;
  if (input.localStopPending || !stopped) {
    items.push({ key: 'stop', tone: 'warning', title: '停止范围尚未被服务端确认', detail: input.localStopPending ? '本机已停止采集，但服务端还没有记录停止范围。联网后请重试确认。' : '课堂仍在采集中。' });
    missing.push('停止范围尚未确认');
  } else {
    items.push({ key: 'stop', tone: 'success', title: '已停止新的采集', detail: `服务端记录的有效采集时长 ${formatClock(session.effective_capture_ms)}${session.known_gap_count ? `，已知中断 ${session.known_gap_count} 段` : ''}${session.unknown_gap_count ? `，未知缺口 ${session.unknown_gap_count} 段` : ''}。` });
  }
  if (input.localPendingChunks > 0) {
    items.push({ key: 'local', tone: 'danger', title: `本机仍有 ${input.localPendingChunks} 个音频分片待上传`, detail: '这些音频只在本机，服务器尚未接收。关闭浏览器不会继续上传，请保持页面打开直到确认。' });
    missing.push(`本机 ${input.localPendingChunks} 个分片待上传`);
  }
  // Chunk numbers restart in every capture epoch, so gaps are counted and named per epoch. Without
  // the whole list (not read, failed, or empty) the audio is never reported complete.
  const gaps = (manifests ?? []).map((item, index) => ({ index, count: countRanges(item.missing_ranges), ranges: item.missing_ranges })).filter(item => item.count > 0);
  const interval = (range: { first_seq: number; last_seq: number }) => range.first_seq === range.last_seq ? `${range.first_seq}` : `${range.first_seq}–${range.last_seq}`;
  const missingChunks = gaps.reduce((n, item) => n + item.count, 0);
  const audioListed = manifests !== null && manifests.length > 0;
  if (manifests === null) {
    if (stopped && input.manifestsUnavailable) { items.push({ key: 'manifest', tone: 'warning', title: '部分原音已过保留期限或已删除', detail: '这部分原音无法再读取，刷新也不能恢复。相关内容只能按部分范围生成总结。' }); missing.push('部分原音已不可读取'); }
    else if (stopped) { items.push({ key: 'manifest', tone: 'info', title: '暂未取得服务端音频清单', detail: '稍后刷新查看。读取完整前不会标记原音完整。' }); missing.push('服务端音频清单尚未读取'); }
  } else if (manifests.length === 0) {
    if (stopped) { items.push({ key: 'manifest', tone: 'warning', title: '服务端没有这节课的原音记录', detail: '没有可确认的原音分片，相关内容不会进入完整总结。' }); missing.push('服务端没有原音记录'); }
  } else if (missingChunks > 0) {
    // Sequence numbers restart in every epoch, so each interval is shown with its own epoch.
    const where = gaps.map(item => `${manifests.length > 1 ? `第 ${item.index + 1} 段采集` : ''}缺分片 ${item.ranges.map(interval).join('、')}（${item.count} 个）`).join('；');
    items.push({ key: 'manifest', tone: 'warning', title: `服务端记录缺少 ${missingChunks} 个分片`, detail: `${where}。缺失范围的原音尚未持久确认，相关内容不会进入完整总结。` });
    missing.push(`原音缺少 ${missingChunks} 个分片`);
  } else if (session.flows.upload === 'durable') {
    // One class-wide verdict: every epoch read without a gap and the server's upload flow durable
    // (an epoch's seal is a separate fact; a resumed-over epoch may stay unsealed).
    const known = manifests.map(item => item.audio_expires_at === null ? NaN : Date.parse(item.audio_expires_at)).filter(value => !Number.isNaN(value));
    const unknown = known.length < manifests.length;
    const earliest = known.length ? new Date(Math.min(...known)).toISOString().slice(0, 10) : null;
    // One shared, fully known deadline is stated as such; otherwise only the earliest known one, with no
    // promise about the others.
    const uniform = !unknown && new Set(known).size === 1;
    const detail = earliest === null ? '原音已持久保存，保留期限未知。'
      : uniform ? `原音保留至 ${earliest}，到期后不能再播放或重新识别。`
        : `已知最早到期的原音保留至 ${earliest}；${unknown ? '部分段落的保留期限未知；' : ''}到期的部分不能再播放或重新识别。`;
    items.push({ key: 'manifest', tone: 'success', title: manifests.length > 1 ? `服务端已确认全部 ${manifests.length} 段采集的原音分片` : '服务端已确认全部原音分片', detail });
  }
  if (manifests?.some(item => item.unknown_provider_ranges.length > 0)) items.push({ key: 'provider', tone: 'warning', title: '部分音频送达转写服务的结果不明', detail: '这部分不会自动重发，以免重复计费。' });
  // The class-wide upload flow is the server's own verdict: until it is durable the material stays partial.
  if (stopped && !input.localStopPending && input.localPendingChunks === 0 && session.flows.upload !== 'durable' && audioListed && missingChunks === 0) {
    items.push({ key: 'upload', tone: 'warning', title: '服务端仍有原音未持久确认', detail: '服务端的上传状态尚未变为已确认。确认前只能按部分范围生成总结。' });
    missing.push('部分原音尚未持久确认');
  }
  const asr = session.flows.asr;
  if (asr === 'succeeded') items.push({ key: 'asr', tone: 'success', title: '转写已完成', detail: '正式字幕已保存。' });
  else if (asr === 'failed' || asr === 'unknown') { items.push({ key: 'asr', tone: asr === 'failed' ? 'danger' : 'warning', title: asr === 'failed' ? '转写失败' : '转写结果未知', detail: '未完成的部分不会自动重新识别。' }); missing.push('转写未完成'); }
  else { items.push({ key: 'asr', tone: 'info', title: asr === 'idle' ? '转写未开始' : '转写仍在处理', detail: '可以等待，也可以对已有内容生成部分范围总结。' }); missing.push('转写仍在处理'); }
  const sync = session.flows.sync;
  if (sync !== 'synced') items.push({ key: 'sync', tone: sync === 'failed' || sync === 'conflict' ? 'danger' : 'warning', title: sync === 'conflict' ? '有版本冲突待处理' : sync === 'failed' ? '同步失败' : '仍有内容未同步', detail: '处理前两个版本都会保留。' });
  const unknownTasks = input.tasks.filter(task => task.state === 'unknown');
  for (const task of unknownTasks) items.push({ key: `task:${task.id}`, tone: 'warning', title: '有任务结果未知，可能已计费', detail: '请先核对结果，系统不会自动重试。' });
  const failed = input.tasks.filter(task => task.state === 'failed');
  if (failed.length) items.push({ key: 'failed', tone: 'danger', title: `${failed.length} 个任务失败`, detail: '失败任务保留原记录。' });
  // All five flows (AI included) plus every task: a queued, running, failed or unknown task is unfinished work.
  const unfinishedTasks = input.tasks.filter(task => task.state === 'queued' || task.state === 'running' || task.state === 'failed' || task.state === 'unknown' || task.state === 'cancel_requested');
  const complete = stopped && !input.localStopPending && input.localPendingChunks === 0 && isClassComplete(session.flows)
    && unfinishedTasks.length === 0 && audioListed && missingChunks === 0;
  const canLeaveSafely = stopped && !input.localStopPending && input.localPendingChunks === 0;
  const leaveWarning = input.localPendingChunks > 0 ? `本机仍有 ${input.localPendingChunks} 个音频分片未上传，离开或关闭页面可能丢失。`
    : input.localStopPending ? '停止范围尚未被服务端确认，离开前请先重试确认。' : null;
  return { items, complete, canLeaveSafely, leaveWarning, missing, unknownTasks };
}

export const TASK_LABEL: Record<S['Task']['kind'], string> = {
  asr_live: '实时转写', asr_retranscribe: '重新转写', ai_question: 'AI 问答', ai_explanation: 'AI 解释', review_summary: '课堂总结', export: '导出', import: '导入', delete: '删除',
};
export const TASK_STATE_LABEL: Record<S['Task']['state'], string> = {
  queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', unknown: '结果未知，可能已计费', cancel_requested: '正在取消', cancelled: '已取消',
};

/** Microyuan strings from the contract → readable yuan without floating-point rounding surprises. */
export function formatYuan(microyuan: string | null): string {
  if (microyuan === null || !/^(0|[1-9][0-9]*)$/.test(microyuan)) return '—';
  // Exact decimal from the integer string: at least two places, sub-cent digits kept (never rounded down).
  const padded = microyuan.padStart(7, '0');
  const yuan = padded.slice(0, -6).replace(/^0+(?=\d)/, '');
  const fraction = padded.slice(-6).replace(/0+$/, '').padEnd(2, '0');
  return `¥${yuan}.${fraction}`;
}

// ---------------------------------------------------------------- drafts

/**
 * In-memory drafts scoped by account epoch and classroom. Switching layout, panels, focus mode or
 * routes keeps them; another classroom never sees them; an account change discards the store.
 * Private text is deliberately not written to localStorage.
 */
export function createDraftStore() {
  const values = new Map<string, string>();
  const listeners = new Set<() => void>();
  let version = 0;
  const notify = () => { version++; listeners.forEach(listener => listener()); };
  return {
    get: (classroomId: string, field: string) => values.get(`${classroomId}\u0000${field}`) ?? '',
    set: (classroomId: string, field: string, value: string) => {
      const key = `${classroomId}\u0000${field}`;
      if ((values.get(key) ?? '') === value) return;
      if (value) values.set(key, value); else values.delete(key);
      notify();
    },
    clear: () => { values.clear(); notify(); },
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    version: () => version,
  };
}
export type DraftStore = ReturnType<typeof createDraftStore>;
