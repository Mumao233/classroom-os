import { CaptureClient } from '../../web/frontend/src/capture/client';
import { CaptureRuntime } from '../../web/frontend/src/capture/runtime';
import { attachPcmWorklet, pcm16 } from '../../web/frontend/src/capture/worklet';

Object.assign(window, { pr05b: { CaptureClient, CaptureRuntime, attachPcmWorklet, pcm16 } });
