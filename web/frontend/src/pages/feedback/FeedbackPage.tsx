import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router';
import { useAppData } from '../../features/data';
import { FeedbackForm } from '../../features/feedback/FeedbackDialog';
import { discardFeedbackContext, feedbackContextStale, readFeedbackContext } from '../../features/feedback/context';
import { useOperations, useSearchEpoch, useSearchHitRevoked } from '../../features/operations';
import styles from './FeedbackPage.module.css';

declare const __BUILD_ID__: string;

/**
 * `/feedback`: build, category and reproduction steps; never the whole classroom text or audio by default.
 * Another page may link here with `{ feedbackContext }` from `stashFeedbackContext`: the request ID and
 * excerpt are looked up for the current account epoch only, and the excerpt is sent only after explicit
 * confirmation. Once the content the excerpt came from is revoked (deleted, or its revisions refused),
 * the excerpt and any consent given for it leave the open form at once and are forgotten; so does an
 * excerpt taken before the search epoch changed (its source's membership became unknown, or the data set
 * was replaced), checked against the stamp it was stored with, also when this page was not open meanwhile.
 * The build is this frontend's own (as shown in Settings), not the service version.
 */
export function FeedbackPage() {
  const { accountKey } = useAppData();
  const operations = useOperations();
  const isRevoked = useSearchHitRevoked();
  const searchEpoch = useSearchEpoch();
  const location = useLocation();
  const reference = (location.state as { feedbackContext?: unknown } | null)?.feedbackContext;
  const stored = readFeedbackContext(accountKey, reference);
  const sourceRevoked = !!stored && (feedbackContextStale(reference, searchEpoch) || (!!stored.source && isRevoked(stored.source)));
  const context = sourceRevoked ? null : stored;
  const [removed, setRemoved] = useState(false);
  useEffect(() => { if (sourceRevoked) { discardFeedbackContext(reference); setRemoved(true); } }, [sourceRevoked, reference]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  const buildId = __BUILD_ID__;
  return <main id="main-content" className={styles.page}>
    <h1 ref={heading} tabIndex={-1}>问题反馈</h1>
    <p>说明遇到的问题，以及可以复现问题的步骤。反馈默认不附带课堂正文、音频或任何密钥。</p>
    {(sourceRevoked || removed) && <p role="status">所选片段的来源已不可访问，片段已移除。</p>}
    <FeedbackForm key={JSON.stringify([accountKey, buildId, context?.requestId ?? null, context?.excerpt ?? null])}
      accountKey={accountKey} buildId={buildId} requestId={context?.requestId ?? null} excerpt={context?.excerpt ?? null}
      submit={operations.submitFeedback} />
  </main>;
}
