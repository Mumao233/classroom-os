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
export function TranscriptTimeline({ entries, emptyText, label }: { entries: readonly TimelineEntry[]; emptyText: string; label: string }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const anchor = useRef<{ key: string; top: number } | null>(null);
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
    const added = keys.filter(key => !previousKeys.current.includes(key)).length;
    previousKeys.current = keys;
    if (following) { box.scrollTop = box.scrollHeight; setUnseen(0); return; }
    const saved = anchor.current;
    if (saved) {
      const element = box.querySelector<HTMLElement>(`[data-entry="${CSS.escape(saved.key)}"]`);
      if (element) box.scrollTop += (element.getBoundingClientRect().top - box.getBoundingClientRect().top) - saved.top;
    }
    if (added) setUnseen(count => count + added);
  }, [entries, following]);

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
        {entries.map(entry => <li key={entry.key} data-entry={entry.key} data-status={entry.status} data-start={entry.startMs ?? ''} className={`${styles.entry} ${entry.status === 'partial' ? styles.entryPartial : ''}`}>
          <span className={styles.entryTime}>{formatClock(entry.startMs)}</span>
          <span>{entry.status === 'partial' && <span className={styles.entryLabel}>临时</span>}{entry.text}</span>
        </li>)}
      </ol>}
      {!following && <button type="button" className={`${styles.buttonSecondary} ${styles.jumpLatest}`} onClick={jump}>{unseen ? `有 ${unseen} 条新内容 · ` : ''}回到最新</button>}
    </div>
    <p className={`${styles.small} ${styles.muted}`}>{following ? '正在跟随最新字幕。' : '回看中：新字幕不会拉动当前位置。'}「临时」字幕会被正式字幕替换，只有正式字幕会保存为课堂依据。</p>
  </div>;
}
