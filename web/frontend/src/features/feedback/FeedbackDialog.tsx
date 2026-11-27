import { useEffect, useId, useRef, useState } from 'react';
import { feedbackCategories, feedbackPayload, type Feedback, type FeedbackAcknowledgement } from '../../domain/feedback';
import { Button, Dialog } from '../../ui';
import styles from './FeedbackDialog.module.css';

type Props = {
  open: boolean;
  onClose: () => void;
  accountKey: string;
  buildId: string;
  requestId?: string | null;
  excerpt?: string | null;
  submit: (payload: Feedback, key: string, signal: AbortSignal) => Promise<FeedbackAcknowledgement>;
};

export function FeedbackDialog({ open, onClose, ...props }: Props) {
  return <Dialog open={open} onOpenChange={value => { if (!value) onClose(); }} title="问题反馈"
    description="说明遇到的问题，以及可以复现问题的步骤。">
    {open && <FeedbackForm key={JSON.stringify([props.accountKey, props.requestId, props.buildId])} {...props} />}
  </Dialog>;
}

export type FeedbackFormProps = Omit<Props, 'open' | 'onClose'>;

/** The form alone, for the `/feedback` page; remount it (key) per account and request context. */
export function FeedbackForm({ buildId, requestId, excerpt, submit }: FeedbackFormProps) {
  const [category, setCategory] = useState<Feedback['category']>('bug');
  const [description, setDescription] = useState('');
  const [consentFor, setConsentFor] = useState<string | null>(null);
  const confirmed = !!excerpt && consentFor === excerpt;
  const [pending, setPending] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [notice, setNotice] = useState('');
  const descId = useId();
  const active = useRef<AbortController | null>(null);
  const intent = useRef<{ payload: string; key: string } | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function send() {
    const payload = feedbackPayload({ category, build_id: buildId, request_id: requestId ?? null, description },
      excerpt ?? null, confirmed);
    if (!payload.description || [...payload.description].length > 5000
        || (payload.confirmed_excerpt && [...payload.confirmed_excerpt].length > 5000)) {
      setNotice('问题说明需为 1 至 5000 个字符，片段最多 5000 个字符。');
      return;
    }
    const fingerprint = JSON.stringify(payload);
    if (intent.current?.payload !== fingerprint) intent.current = { payload: fingerprint, key: crypto.randomUUID() };
    const run = new AbortController();
    active.current = run;
    setPending(true); setNotice('');
    try {
      const result = await submit(payload, intent.current.key, run.signal);
      if (run.signal.aborted) return;
      if (result.accepted) {
        setAccepted(true); setDescription(''); setConsentFor(null); intent.current = null;
      } else setNotice('反馈未提交，请重试。');
    } catch {
      if (!run.signal.aborted) setNotice('暂时无法提交，请重试。');
    } finally {
      if (!run.signal.aborted) setPending(false);
    }
  }

  if (accepted) return <p role="status">反馈已提交。</p>;
  return <form className={styles.form} onSubmit={event => { event.preventDefault(); void send(); }}>
    <label>反馈类型<select value={category} disabled={pending} onChange={event => setCategory(event.target.value as Feedback['category'])}>
      {Object.entries(feedbackCategories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    <label htmlFor={descId}>问题说明</label>
    <textarea id={descId} rows={5} value={description} maxLength={10000} disabled={pending}
      onChange={event => setDescription(event.target.value)} required />
    {excerpt && <fieldset className={styles.consent} disabled={pending}>
      <legend>课堂片段</legend>
      <label><input type="checkbox" checked={confirmed} onChange={event => setConsentFor(event.target.checked ? excerpt : null)} />确认随反馈发送此片段</label>
      <pre className={styles.excerpt}>{excerpt}</pre>
    </fieldset>}
    <p role="status">{notice}</p>
    <Button type="submit" disabled={pending}>{pending ? '正在提交…' : '提交反馈'}</Button>
  </form>;
}
