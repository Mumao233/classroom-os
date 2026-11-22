import type { components } from '../generated/openapi';

type Scope = { epoch: number; isCurrent: (epoch: number) => boolean };
type S = components['schemas'];
type ErrorBody = S['Error'];
const PREFIX = '/api/web/v1';

/** Errors from transport, HTTP responses and invalidated account scopes share one boundary. */
export class ApiClientError extends Error {
  constructor(readonly kind: 'stale' | 'network' | 'http' | 'invalid_response', readonly status?: number, readonly detail?: ErrorBody) {
    super(`API request failed: ${kind}`);
    this.name = 'ApiClientError';
  }
  /** The approved error code, only when the body has the Error envelope shape. */
  get code(): ErrorBody['error']['code'] | undefined {
    const value = this.detail as unknown;
    if (typeof value !== 'object' || value === null || !('error' in value)) return undefined;
    const error = (value as { error: unknown }).error;
    return typeof error === 'object' && error !== null && typeof (error as { code?: unknown }).code === 'string' ? (error as { code: ErrorBody['error']['code'] }).code : undefined;
  }
  get currentRevision(): number | null {
    if (!this.code) return null;
    const revision = (this.detail as ErrorBody).error.current_revision;
    return typeof revision === 'number' ? revision : null;
  }
}

export type ApiOptions = {
  transport?: typeof fetch;
  /** Same-origin CSRF token supplied by the authenticated session context; never persisted here. */
  csrfToken?: () => string | null;
  /** Fresh idempotency key per user intent. Injectable for deterministic tests. */
  newKey?: () => string;
};

type Query = Record<string, string | number | boolean | null | undefined>;
const withQuery = (path: string, query: Query = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  const text = params.toString();
  return `${PREFIX}${path}${text ? `?${text}` : ''}`;
};
const segment = (value: string) => encodeURIComponent(value);

/** A request belongs to the account epoch captured by its caller, including during body parsing. */
export function createApiClient(scope: Scope, transportOrOptions: typeof fetch | ApiOptions = fetch) {
  const options: ApiOptions = typeof transportOrOptions === 'function' ? { transport: transportOrOptions } : transportOrOptions;
  const transport = options.transport ?? fetch;
  const newKey = options.newKey ?? (() => crypto.randomUUID());
  const assertCurrent = () => { if (!scope.isCurrent(scope.epoch)) throw new ApiClientError('stale'); };

  async function request<T>(method: 'GET' | 'POST' | 'PATCH', url: string, body?: unknown, signal?: AbortSignal, key?: string): Promise<T> {
    assertCurrent();
    let response: Response;
    try {
      if (method === 'GET') {
        response = await transport(url, { method, credentials: 'same-origin', cache: 'no-store', signal });
      } else {
        const headers: Record<string, string> = { 'Content-Type': 'application/json', 'Idempotency-Key': key ?? newKey() };
        const csrf = options.csrfToken?.();
        if (csrf) headers['X-CSRF-Token'] = csrf;
        response = await transport(url, { method, credentials: 'same-origin', cache: 'no-store', signal, headers, body: JSON.stringify(body ?? {}) });
      }
    } catch (error) {
      assertCurrent();
      if (signal?.aborted) throw error;
      throw new ApiClientError('network');
    }
    assertCurrent();
    let parsed: unknown;
    try { parsed = await response.json(); } catch {
      assertCurrent();
      throw new ApiClientError(response.ok ? 'invalid_response' : 'http', response.status);
    }
    assertCurrent();
    if (!response.ok) throw new ApiClientError('http', response.status, parsed as ErrorBody);
    return parsed as T;
  }
  /** Raw bytes to a server-issued same-origin upload path (media content). */
  async function putBytes<T>(url: string, bytes: Uint8Array<ArrayBuffer>, digest: string, key: string): Promise<T> {
    assertCurrent();
    if (!url.startsWith(`${PREFIX}/`)) throw new ApiClientError('invalid_response');
    let response: Response;
    // The contract accepts only octet-stream bytes here; the image type travels in the upload descriptor.
    const headers: Record<string, string> = { 'Content-Type': 'application/octet-stream', 'Content-Digest': `sha-256=:${digest}:`, 'Idempotency-Key': key };
    const csrf = options.csrfToken?.();
    if (csrf) headers['X-CSRF-Token'] = csrf;
    try { response = await transport(url, { method: 'PUT', credentials: 'same-origin', cache: 'no-store', headers, body: bytes }); }
    catch { assertCurrent(); throw new ApiClientError('network'); }
    assertCurrent();
    let parsed: unknown;
    try { parsed = await response.json(); } catch { assertCurrent(); throw new ApiClientError(response.ok ? 'invalid_response' : 'http', response.status); }
    assertCurrent();
    if (!response.ok) throw new ApiClientError('http', response.status, parsed as ErrorBody);
    return parsed as T;
  }
  const get = <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>('GET', withQuery(path, query), undefined, signal);
  /** Every page of a cursor list, at the contract's maximum page size of 100. */
  async function allPages<T>(path: string, signal?: AbortSignal, filter: Query = {}): Promise<{ items: T[]; next_cursor: null }> {
    const items: T[] = [];
    let cursor: string | null = null;
    for (let pages = 0; pages < 1000; pages++) {
      const page: { items: T[]; next_cursor: string | null } = await get(path, cursor ? { ...filter, limit: 100, cursor } : { ...filter, limit: 100 }, signal);
      items.push(...page.items);
      if (!page.next_cursor) return { items, next_cursor: null };
      cursor = page.next_cursor;
    }
    throw new ApiClientError('invalid_response');
  }
  const post = <T>(path: string, body: unknown, key?: string) => request<T>('POST', withQuery(path), body, undefined, key);
  const patch = <T>(path: string, body: unknown, key?: string) => request<T>('PATCH', withQuery(path), body, undefined, key);

  return {
    /** Retained PR-09a entry: unfiltered first page. */
    getCourses: (signal?: AbortSignal) => request<S['CoursePage']>('GET', `${PREFIX}/courses`, undefined, signal),
    meta: (signal?: AbortSignal) => get<S['Meta']>('/meta', undefined, signal),
    me: (signal?: AbortSignal) => get<S['User']>('/me', undefined, signal),
    updateProfile: (body: S['ProfilePatch']) => patch<S['User']>('/me', body),
    preferences: (signal?: AbortSignal) => get<S['Preferences']>('/me/preferences', undefined, signal),
    updatePreferences: (body: S['PreferencesPatch']) => patch<S['Preferences']>('/me/preferences', body),
    quota: (signal?: AbortSignal) => get<S['QuotaView']>('/me/quota', undefined, signal),
    sessions: (signal?: AbortSignal) => get<S['DeviceSessionPage']>('/me/sessions', { limit: 100 }, signal),
    revokeSession: (id: string) => post<S['Acknowledged']>(`/me/sessions/${segment(id)}/revoke`, {}),
    devices: (signal?: AbortSignal) => get<S['TrustedDevicePage']>('/me/devices', { limit: 100 }, signal),
    revokeDevice: (id: string) => post<S['Acknowledged']>(`/me/devices/${segment(id)}/revoke`, {}),

    semesters: (signal?: AbortSignal) => get<S['SemesterPage']>('/semesters', { limit: 100 }, signal),
    semester: (id: string, signal?: AbortSignal) => get<S['Semester']>(`/semesters/${segment(id)}`, undefined, signal),
    createSemester: (body: S['SemesterCreate'], key?: string) => post<S['Semester']>('/semesters', body, key),
    updateSemester: (id: string, body: S['SemesterPatch']) => patch<S['Semester']>(`/semesters/${segment(id)}`, body),
    semesterDeletionPreview: (id: string) => get<S['DeletionPreview']>(`/semesters/${segment(id)}/deletion-preview`),
    deleteSemester: (id: string, body: S['DeleteScopeRequest']) => post<S['Deletion']>(`/semesters/${segment(id)}/delete`, body),

    courses: (query: { semester_id?: string; archived?: boolean; q?: string; sort?: 'recent' | 'name' }, signal?: AbortSignal) => get<S['CoursePage']>('/courses', { ...query, limit: 100 }, signal),
    course: (id: string, signal?: AbortSignal) => get<S['Course']>(`/courses/${segment(id)}`, undefined, signal),
    createCourse: (body: S['CourseCreate'], key?: string) => post<S['Course']>('/courses', body, key),
    updateCourse: (id: string, body: S['CoursePatch']) => patch<S['Course']>(`/courses/${segment(id)}`, body),
    courseDeletionPreview: (id: string) => get<S['DeletionPreview']>(`/courses/${segment(id)}/deletion-preview`),
    deleteCourse: (id: string, body: S['DeleteScopeRequest']) => post<S['Deletion']>(`/courses/${segment(id)}/delete`, body),
    materials: (courseId: string, signal?: AbortSignal) => get<S['CourseMaterialPage']>(`/courses/${segment(courseId)}/materials`, undefined, signal),
    writeMaterial: (courseId: string, body: S['CourseMaterialWrite']) => post<S['CourseMaterial']>(`/courses/${segment(courseId)}/materials`, body),

    classSessions: (query: { course_id?: string }, signal?: AbortSignal) => get<S['ClassSessionPage']>('/class-sessions', { ...query, limit: 100 }, signal),
    updateClassSession: (id: string, body: S['ClassSessionPatch']) => patch<S['ClassSession']>(`/class-sessions/${segment(id)}`, body),
    reviewDocuments: (query: { course_id?: string; kind?: 'summary' | 'card' | 'note'; understood?: boolean }, signal?: AbortSignal) => get<S['ReviewDocumentPage']>('/review-documents', { ...query, limit: 100 }, signal),
    questions: (classSessionId: string, signal?: AbortSignal) => get<S['QuestionPage']>('/questions', { class_session_id: classSessionId, limit: 100 }, signal),
    notifications: (signal?: AbortSignal) => get<S['NotificationPage']>('/notifications', { limit: 50 }, signal),

    // PR-10a classroom, capture and processing. Owner always comes from the server session.
    classSession: (id: string, signal?: AbortSignal) => get<S['ClassSession']>(`/class-sessions/${segment(id)}`, undefined, signal),
    createClassSession: (body: S['ClassSessionCreate'], key?: string) => post<S['ClassSession']>('/class-sessions', body, key),
    acquireCapture: (id: string, body: S['CaptureAcquire'], key?: string) => post<S['CaptureLease']>(`/class-sessions/${segment(id)}/capture/acquire`, body, key),
    captureCommand: (id: string, action: 'pause' | 'resume', body: S['CaptureCommand'], key?: string) => post<S['CaptureLease']>(`/class-sessions/${segment(id)}/capture/${action}`, body, key),
    stopCapture: (id: string, body: S['CaptureStop'], key?: string) => post<S['AudioManifest']>(`/class-sessions/${segment(id)}/capture/stop`, body, key),
    audioManifest: (id: string, captureEpochId: string | null, signal?: AbortSignal) => get<S['AudioManifest']>(`/class-sessions/${segment(id)}/audio-manifest`, captureEpochId ? { capture_epoch_id: captureEpochId } : undefined, signal),
    transcriptVersions: (id: string, signal?: AbortSignal) => allPages<S['TranscriptVersion']>(`/class-sessions/${segment(id)}/transcript-versions`, signal),
    transcriptSegments: (versionId: string, signal?: AbortSignal) => allPages<S['TranscriptSegment']>(`/transcript-versions/${segment(versionId)}/segments`, signal),
    marks: (id: string, signal?: AbortSignal) => allPages<S['Mark']>(`/class-sessions/${segment(id)}/marks`, signal),
    createMark: (id: string, body: S['MarkCreate'], key?: string) => post<S['Mark']>(`/class-sessions/${segment(id)}/marks`, body, key),
    slides: (id: string, signal?: AbortSignal) => allPages<S['Slide']>(`/class-sessions/${segment(id)}/slides`, signal),
    createMediaUpload: (body: S['MediaUploadCreate'], key?: string) => post<S['MediaUpload']>('/media/uploads', body, key),
    putMediaContent: (upload: S['MediaUpload'], bytes: Uint8Array<ArrayBuffer>, digestBase64: string, key: string) => putBytes<S['MediaUpload']>(upload.upload_path, bytes, digestBase64, key),
    completeMediaUpload: (uploadId: string, body: S['MediaVerify'], key?: string) => post<S['MediaAsset']>(`/media/uploads/${segment(uploadId)}/complete`, body, key),
    createSlide: (id: string, body: S['SlideCreate'], key?: string) => post<S['Slide']>(`/class-sessions/${segment(id)}/slides`, body, key),
    updateSlide: (slideId: string, body: S['SlidePatch'], key: string) => patch<S['Slide']>(`/slides/${segment(slideId)}`, body, key),
    createPlan: (id: string, body: S['GenerationPlanRequest'], key?: string) => post<S['GenerationPlan']>(`/class-sessions/${segment(id)}/generation-plans`, body, key),
    plan: (planId: string, signal?: AbortSignal) => get<S['GenerationPlan']>(`/generation-plans/${segment(planId)}`, undefined, signal),
    executePlan: (planId: string, body: S['ExecutePlan'], key?: string) => post<S['Task']>(`/generation-plans/${segment(planId)}/execute`, body, key),
    tasks: (classSessionId: string, signal?: AbortSignal) => allPages<S['Task']>('/tasks', signal, { class_session_id: classSessionId }),
    task: (taskId: string, signal?: AbortSignal) => get<S['Task']>(`/tasks/${segment(taskId)}`, undefined, signal),
    reconcileTask: (taskId: string, key?: string) => post<S['TaskReconcile']>(`/tasks/${segment(taskId)}/reconcile`, {}, key),
    cancelTask: (taskId: string, key?: string) => post<S['Task']>(`/tasks/${segment(taskId)}/cancel`, {}, key),
  };
}
export type ApiClient = ReturnType<typeof createApiClient>;
