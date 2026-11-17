import workletUrl from '../../../../static/pcm-worklet.js?url';
import { CaptureError } from './ports';

export function pcm16(samples: Float32Array): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => {
    const bounded = Math.max(-1, Math.min(1, Number.isFinite(sample) ? sample : 0));
    view.setInt16(index * 2, Math.round(bounded * (bounded < 0 ? 32768 : 32767)), true);
  });
  return bytes;
}

/** No microphone acquisition. Callers supply an explicitly authorized AudioNode or a synthetic test source. */
export async function attachPcmWorklet(context: AudioContext, source: AudioNode,
  append: (bytes: Uint8Array<ArrayBuffer>) => Promise<void>, maxBufferedBytes = 64000) {
  if (!Number.isSafeInteger(maxBufferedBytes) || maxBufferedBytes < 32000) throw new CaptureError('INVALID_CAPACITY');
  await context.audioWorklet.addModule(workletUrl);
  const node = new AudioWorkletNode(context, 'classroom-pcm');
  const pending: number[] = [];
  let writes = Promise.resolve();
  let failure: unknown;
  let closed = false;
  let bufferedBytes = 0;
  let disconnected = false;
  const disconnect = () => { if (!disconnected) { source.disconnect(node); disconnected = true; } };
  const flushes = new Map<string, () => void>();
  const submit = (samples: Float32Array) => {
    const bytes = pcm16(samples);
    if (failure || bufferedBytes + bytes.length > maxBufferedBytes) {
      failure ??= new CaptureError('CAPACITY_EXCEEDED'); disconnect(); return;
    }
    bufferedBytes += bytes.length;
    writes = writes.then(async () => {
      if (failure) return;
      await append(bytes);
    }).catch(error => { failure = error; disconnect(); }).finally(() => { bufferedBytes -= bytes.length; });
  };
  node.port.onmessage = ({ data }: MessageEvent<{ type: string; samples?: Float32Array; token?: string }>) => {
    if (data.type === 'pcm' && data.samples && !failure) {
      pending.push(...data.samples);
      while (pending.length >= 16000) submit(new Float32Array(pending.splice(0, 16000)));
    }
    if (data.type === 'flushed' && data.token) { flushes.get(data.token)?.(); flushes.delete(data.token); }
  };
  source.connect(node);
  node.connect(context.destination);
  return {
    status() { return { bufferedBytes, failed: Boolean(failure), disconnected }; },
    async stop(): Promise<void> {
      if (closed) return;
      closed = true;
      disconnect();
      const token = crypto.randomUUID();
      let timer: ReturnType<typeof setTimeout>;
      try {
        await new Promise<void>((resolve, reject) => {
          timer = setTimeout(() => reject(new CaptureError('WORKLET_FLUSH_TIMEOUT')), 3000);
          flushes.set(token, resolve);
          node.port.postMessage({ type: 'flush', token });
        });
        if (pending.length) submit(new Float32Array(pending.splice(0)));
        await writes;
        if (failure) throw failure;
      } finally { clearTimeout(timer!); node.disconnect(); node.port.close(); }
    },
  };
}
