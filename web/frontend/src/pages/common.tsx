import { useEffect, useRef, type ReactNode } from 'react';
import { IconAlertTriangle, IconAtom, IconBook2, IconCode, IconFlask, IconLanguage, IconMathFunction, IconWifiOff } from '@tabler/icons-react';
import type { components } from '../generated/openapi';
import { flowItems, summarizeFlows, type FlowStates } from '../domain/flows';
import { normalizeColor } from '../domain/courses';
import { describeError, useAppData } from '../features/data';
import styles from './pages.module.css';

/** Page heading that takes focus on mount so route changes are announced (tabs, forms). */
export function PageHeading({ title, detail, actions, eyebrow, focusKey }: { title: ReactNode; detail?: ReactNode; actions?: ReactNode; eyebrow?: string; focusKey?: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusKey !== undefined) ref.current?.focus({ preventScroll: true }); }, [focusKey]);
  return <div className={styles.heading}>
    <div className={styles.wrap}>{eyebrow && <p>{eyebrow}</p>}<h1 ref={ref} tabIndex={-1}>{title}</h1>{detail && <p>{detail}</p>}</div>
    {actions && <div className={styles.row}>{actions}</div>}
  </div>;
}

export function DataSourceNote() {
  const { dataSourceLabel } = useAppData();
  return dataSourceLabel ? <p className={`${styles.small} ${styles.muted}`} data-testid="data-source">{dataSourceLabel}</p> : null;
}

/** UI-DASH-09 / UI-COURSE-11: offline scope is explicit, never implied. */
export function OfflineBanner({ scope }: { scope: string }) {
  const { online } = useAppData();
  if (online) return null;
  return <div className={`${styles.banner} ${styles.bannerWarning}`} role="status" data-testid="offline-banner">
    <IconWifiOff size={18} aria-hidden="true" /><div><strong>当前离线</strong>：{scope} 学习缓存与离线同步尚未启用（PR-08a/08b），新建、编辑、归档、删除和改归属均已停用。</div>
  </div>;
}

export function QueryError({ error, retry }: { error: unknown; retry?: () => void }) {
  return <div className={`${styles.banner} ${styles.bannerDanger}`} role="alert"><IconAlertTriangle size={18} aria-hidden="true" />
    <div>{describeError(error)} {retry && <button type="button" className={styles.buttonGhost} onClick={retry}>重新读取</button>}</div></div>;
}
export function Loading({ label }: { label: string }) { return <p role="status" className={styles.muted}>{label}</p>; }

export function Empty({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <div className={styles.empty}><h3>{title}</h3><p>{description}</p>{children}</div>;
}

const toneClass = (tone: string) => (styles as Record<string, string>)[tone] ?? '';
export function Badge({ tone = 'neutral', children }: { tone?: string; children: ReactNode }) {
  return <span className={`${styles.badge} ${toneClass(tone)}`}>{children}</span>;
}

/** UI-DASH-07: normal summary, abnormal expanded; each flow named, never one "done" badge for a failure. */
export function FlowSummaryView({ flows, expandAll = false }: { flows: FlowStates; expandAll?: boolean }) {
  const summary = summarizeFlows(flows);
  const shown = expandAll ? flowItems(flows) : summary.abnormal;
  return <div data-testid="flow-summary" data-complete={String(summary.complete)}>
    <Badge tone={summary.tone}>{summary.complete ? '✓ ' : summary.abnormal.length ? '! ' : '◌ '}{summary.headline}</Badge>
    {shown.length > 0 && <ul className={styles.flows} aria-label="五项独立状态">{shown.map(item => <li key={item.key}><Badge tone={item.tone}>{item.name}：{item.label}</Badge></li>)}</ul>}
  </div>;
}

const icons = { book: IconBook2, math: IconMathFunction, atom: IconAtom, code: IconCode, language: IconLanguage, flask: IconFlask } as const;
export function CourseGlyph({ course }: { course: Pick<components['schemas']['Course'], 'icon_token' | 'name'> }) {
  const Icon = icons[course.icon_token as keyof typeof icons];
  return <span className={styles.glyph} aria-hidden="true">{Icon ? <Icon size={20} /> : (course.icon_token || course.name).slice(0, 2)}</span>;
}
export const coverClass = (token: string) => styles[normalizeColor(token)];
