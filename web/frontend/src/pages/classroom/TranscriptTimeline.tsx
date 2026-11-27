import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { formatClock, type TimelineEntry } from '../../domain/classroom';
import styles from './classroom.module.css';

const FOLLOW_SLACK_PX = 32;

/**
 * UI-CLASS-07 / ASR-04 timeline. While the reader is at the end it follows new text; once they
 * scroll back it stops following, and an entry inserted above the viewport (a late final) keeps
 * the visible text in place instead of pulling the view. Partial text is labelled and styled
 * differently from final text and is never announced through a live region.
 */
/** A formal segment to open at (PR-11c search hit); highlighted only while its exact revision is shown. */
/** `token`: identifies one explicit opening (the navigation), so opening the same segment again scrolls again. */
export type TimelineFocus = { segmentId: string; segmentRevisionId: string | null; token?: string };

export function TranscriptTimeline({ entries, emptyText, label, focus = null }: { entries: readonly TimelineEntry[]; emptyText: string; label: string; focus?: TimelineFocus | null }) {
  const scroller = useRef<HTMLDivElement>(null);
  // Opening at a segment starts in review mode, so following the newest text does not pull the view away.
  const [following, setFollowing] = useState(!focus);
  /** The segment/revision pair last scrolled into view. */
  const focusedOnce = useRef<string | null>(null);
  const [unseen, setUnseen] = useState(0);
  const anchor = useRef<{ key: string; top: number } | null>(null);
  /** Entry keys already shown; the first loaded transcript is the baseline, never "new" content. */
  const previousKeys = useRef<string[]>([]);

  // Before the DOM changes, remember which entry sits at the top of the viewport.
  const captureAnchor = () => {
    const box = scroller.current;
    if (!box) return;
    const top = box.getBoundingClientRect().top;
    for (const item of box.querySelectorAll<HTMLElement>('[data-entry]')) {
      const rect = item.getBoundingClientRect();
      if (rect.bottom > top + 1) { anchor.current = { key: item.dataset.entry!, top: rect.top - top }; return; }
    }
    anchor.current = null;
  };

  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    const keys = entries.map(entry => entry.key);
    const baseline = previousKeys.current.length === 0;
    const added = baseline ? 0 : keys.filter(key => !previousKeys.current.includes(key)).length;
    previousKeys.current = keys;
    if (following) { box.scrollTop = box.scrollHeight; setUnseen(0); return; }
    const saved = anchor.current;
    if (saved) {
      const element = box.querySelector<HTMLElement>(`[data-entry="${CSS.escape(saved.key)}"]`);
      if (element) box.scrollTop += (element.getBoundingClientRect().top - box.getBoundingClientRect().top) - saved.top;
    }
    if (added) setUnseen(count => count + added);
  }, [entries, following]);

  // A newly opened segment (another hit in the same classroom) stops following before it is scrolled to.
  const focusKey = focus ? `${focus.segmentId}:${focus.segmentRevisionId ?? ''}:${focus.token ?? ''}` : null;
  const [appliedFocus, setAppliedFocus] = useState(focusKey);
  if (focusKey !== appliedFocus) {
    setAppliedFocus(focusKey);
    if (focusKey) setFollowing(false);
  }
  // Scroll each opened segment into view once it is present (after the anchor logic above).
  useLayoutEffect(() => {
    const box = scroller.current;
    if (!focus || focusKey === null || focusedOnce.current === focusKey || !box) return;
    const target = entries.find(entry => entry.status === 'final' && entry.segmentId === focus.segmentId
      && (focus.segmentRevisionId === null || entry.segmentRevisionId === focus.segmentRevisionId));
    const element = target && [...box.querySelectorAll<HTMLElement>('[data-entry]')].find(item => item.dataset.entry === target.key);
    if (!element) return;
    focusedOnce.current = focusKey;
    box.scrollTop += element.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 3;
  }, [entries, focus, focusKey]);

  // The anchor is refreshed on every scroll so the next update preserves the reader's place.
  useEffect(() => { captureAnchor(); });

  const onScroll = () => {
    const box = scroller.current;
    if (!box) return;
    const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight <= FOLLOW_SLACK_PX;
    if (atEnd !== following) setFollowing(atEnd);
    if (atEnd) setUnseen(0);
    captureAnchor();
  };
  const jump = () => {
    const box = scroller.current;
    if (!box) return;
    box.scrollTop = box.scrollHeight;
    setFollowing(true);
    setUnseen(0);
  };

  return <div className={styles.workspace}>
    <div ref={scroller} className={styles.timeline} onScroll={onScroll} tabIndex={0} role="region" aria-label={label} data-testid="transcript-timeline" data-following={String(following)}>
      {entries.length === 0 ? <p className={styles.muted}>{emptyText}</p> : <ol>
        {entries.map(entry => { const focused = !!focus && entry.status === 'final' && entry.segmentId === focus.segmentId
          && (focus.segmentRevisionId === null || entry.segmentRevisionId === focus.segmentRevisionId);
          return <li key={entry.key} data-entry={entry.key} data-status={entry.status} data-start={entry.startMs ?? ''} data-segment-id={entry.segmentId}
          aria-current={focused ? 'location' : undefined} className={`${styles.entry} ${entry.status === 'partial' ? styles.entryPartial : ''} ${focused ? styles.entryFocused : ''}`}>
          <span className={styles.entryTime}>{formatClock(entry.startMs)}</span>
          <span>{entry.status === 'partial' && <span className={styles.entryLabel}>临时</span>}{entry.text}</span>
        </li>; })}
      </ol>}
      {!following && <button type="button" className={`${styles.buttonSecondary} ${styles.jumpLatest}`} onClick={jump}>{unseen ? `有 ${unseen} 条新内容 · ` : ''}回到最新</button>}
    </div>
    <p className={`${styles.small} ${styles.muted}`}>{following ? '正在跟随最新字幕。' : '回看中：新字幕不会拉动当前位置。'}「临时」字幕会被正式字幕替换，只有正式字幕会保存为课堂依据。</p>
  </div>;
}
