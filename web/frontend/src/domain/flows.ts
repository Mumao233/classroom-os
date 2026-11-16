import type { components } from '../generated/openapi';

export type FlowStates = components['schemas']['FlowStates'];
export type FlowKey = keyof FlowStates;
export type FlowTone = 'neutral' | 'success' | 'info' | 'warning' | 'danger' | 'recording' | 'offline';
export type FlowItem = { key: FlowKey; name: string; label: string; tone: FlowTone; abnormal: boolean };

const names: Record<FlowKey, string> = { capture: '录音', upload: '上传', asr: '转写', ai: 'AI', sync: '同步' };
const labels: { [K in FlowKey]: Record<FlowStates[K], [string, FlowTone, boolean]> } = {
  capture: { idle: ['未开始', 'neutral', false], recording: ['采集中', 'recording', false], paused: ['已暂停', 'info', false], local_only: ['仅本地采集', 'warning', true], failed: ['采集故障', 'danger', true], ended: ['已结束', 'neutral', false] },
  upload: { pending: ['待上传', 'info', false], uploading: ['上传中', 'info', false], durable: ['已持久确认', 'success', false], failed: ['上传失败', 'danger', true] },
  asr: { idle: ['未开始', 'neutral', false], streaming: ['转写中', 'info', false], catching_up: ['追赶中', 'info', false], draining: ['收尾中', 'info', false], succeeded: ['已完成', 'success', false], failed: ['转写失败', 'danger', true], unknown: ['结果未知', 'warning', true] },
  ai: { idle: ['无任务', 'neutral', false], queued: ['排队中', 'info', false], running: ['生成中', 'info', false], succeeded: ['已完成', 'success', false], failed: ['生成失败', 'danger', true], unknown: ['结果未知，可能已计费', 'warning', true], cancel_requested: ['正在取消', 'info', false], cancelled: ['已取消', 'neutral', false] },
  sync: { synced: ['已同步', 'success', false], local_only: ['仅本机，未同步', 'warning', true], syncing: ['同步中', 'info', false], failed: ['同步失败', 'danger', true], conflict: ['版本冲突', 'warning', true] },
};
const order: FlowKey[] = ['capture', 'upload', 'asr', 'ai', 'sync'];

/** Five independent items; there is deliberately no single "isComplete" shortcut in this list. */
export function flowItems(flows: FlowStates): FlowItem[] {
  return order.map(key => {
    const [label, tone, abnormalState] = (labels[key] as Record<string, [string, FlowTone, boolean]>)[flows[key]];
    // An ended capture whose audio is still pending upload is a real risk, not a neutral wait.
    const pendingAfterEnd = key === 'upload' && flows.upload === 'pending' && flows.capture === 'ended';
    return { key, name: names[key], label: pendingAfterEnd ? '录音已结束，仍待上传' : label, tone: pendingAfterEnd ? 'warning' : tone, abnormal: abnormalState || pendingAfterEnd };
  });
}

/**
 * Whole-class completion requires every flow to have independently finished. Upload failure
 * with otherwise healthy capture (UI-DASH-07) is therefore never complete.
 */
export function isClassComplete(flows: FlowStates): boolean {
  return flows.capture === 'ended' && flows.upload === 'durable' && flows.asr === 'succeeded' && flows.sync === 'synced'
    && (flows.ai === 'idle' || flows.ai === 'succeeded' || flows.ai === 'cancelled');
}

export type FlowSummary = { complete: boolean; abnormal: FlowItem[]; headline: string; tone: FlowTone };
/** Normal state is summarised; any abnormal flow is expanded individually. */
export function summarizeFlows(flows: FlowStates): FlowSummary {
  const items = flowItems(flows);
  const abnormal = items.filter(item => item.abnormal);
  if (isClassComplete(flows)) return { complete: true, abnormal: [], headline: '五项处理均已完成', tone: 'success' };
  if (abnormal.length) return { complete: false, abnormal, headline: `${abnormal.length} 项需要处理`, tone: abnormal.some(item => item.tone === 'danger') ? 'danger' : 'warning' };
  if (flows.capture === 'recording' || flows.capture === 'paused') return { complete: false, abnormal, headline: flows.capture === 'recording' ? '正在采集' : '采集已暂停', tone: 'recording' };
  return { complete: false, abnormal, headline: '处理中，尚未全部完成', tone: 'info' };
}
