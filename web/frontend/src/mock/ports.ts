import type { ClassroomOSWebV1EventEnvelope } from '../generated/events';

/** Deterministic in-memory test ports. None accesses a device, disk or network. */
export class MockCapturePort {
  state: 'idle' | 'recording' | 'paused' | 'local_only' | 'failed' | 'ended' = 'idle';
  durationMs = 0;
  command(next: typeof this.state): boolean {
    const allowed: Record<typeof this.state, Array<typeof this.state>> = {
      idle: ['recording'], recording: ['paused', 'local_only', 'failed', 'ended'],
      paused: ['recording', 'failed', 'ended'], local_only: ['recording', 'failed', 'ended'],
      failed: ['ended'], ended: [],
    };
    if (!allowed[this.state].includes(next)) return false;
    this.state = next;
    return true;
  }
  advance(milliseconds: number) { if (this.state === 'recording' || this.state === 'local_only') this.durationMs += Math.max(0, milliseconds); }
}

/** The caller supplies validated, synthetic events and controls delivery order. */
export class MockEventClient {
  private listeners = new Set<(event: ClassroomOSWebV1EventEnvelope) => void>();
  subscribe(listener: (event: ClassroomOSWebV1EventEnvelope) => void) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  emit(event: ClassroomOSWebV1EventEnvelope) { this.listeners.forEach(listener => listener(event)); }
  clear() { this.listeners.clear(); }
  listenerCount() { return this.listeners.size; }
}

export class MockUploadPort {
  private chunks = new Set<number>();
  stage(seq: number) { this.chunks.add(seq); }
  frameReceived(seq: number) { return this.chunks.has(seq); /* A frame acknowledgement is not durable. */ }
  durable(seq: number) { this.chunks.delete(seq); }
  pending(seq: number) { return this.chunks.has(seq); }
}

/** Test-only memory; it is not an encrypted store or persistence implementation. */
export class MemoryVault {
  private locked = true;
  private records = new Map<string, string>();
  constructor(private readonly quotaBytes = 1024) {}
  unlock() { this.locked = false; }
  lock() { this.locked = true; }
  write(key: string, value: string, fail = false): 'stored' | 'locked' | 'quota' | 'write_failed' {
    if (this.locked) return 'locked';
    if (fail) return 'write_failed';
    const next = new Map(this.records); next.set(key, value);
    const bytes = [...next].reduce((n, [k, v]) => n + new TextEncoder().encode(k + v).length, 0);
    if (bytes > this.quotaBytes) return 'quota';
    this.records = next;
    return 'stored';
  }
  read(key: string) { return this.locked ? undefined : this.records.get(key); }
  clear() { this.records.clear(); }
}
