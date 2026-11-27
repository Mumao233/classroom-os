import type { RefObject } from 'react';
import { useNavigate } from 'react-router';
import { useAppData } from '../data';
import { stashFeedbackContext } from './context';
import type { SearchHitRef } from '../revocation';

/** The backend accepts at most 5000 characters of confirmed excerpt. */
const EXCERPT_LIMIT = 5000;

/** Text the user selected inside `within`, if any (never the whole page). */
function selectedText(within: HTMLElement | null): string | null {
  const selection = typeof window === 'undefined' ? null : window.getSelection();
  if (!within || !selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  if (!within.contains(range.commonAncestorContainer)) return null;
  const text = selection.toString().trim();
  return text ? [...text].slice(0, EXCERPT_LIMIT).join('') : null;
}

/**
 * A contextual "report a problem" action: text the user selected inside `within` is offered as the
 * excerpt. It is stashed under the current account epoch, bound to `source` (the content it was taken
 * from, judged like a search hit), and only an opaque reference is navigated
 * with; the feedback form still requires a separate confirmation before the excerpt is sent.
 */
export function FeedbackLink({ within, source, className }: { within: RefObject<HTMLElement | null>; source: SearchHitRef; className?: string }) {
  const { accountKey, revocation } = useAppData();
  const navigate = useNavigate();
  return <button type="button" className={className} onMouseDown={event => event.preventDefault()} onClick={() => {
    const excerpt = selectedText(within.current);
    const state = excerpt ? { feedbackContext: stashFeedbackContext(accountKey, { requestId: null, excerpt, source }, revocation?.searchEpoch() ?? 0) } : undefined;
    navigate('/feedback', { state });
  }}>反馈问题{' '}<small>（可先选中相关文字）</small></button>;
}
