import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { MIN_WIDTH, columnWidths, defaultLayout, layoutMode, moveSeparator, parseLayout, type LayoutMode, type LayoutPrefs, type PanelKey } from '../../domain/classroom';
import { useClassroomShell } from '../../features/classroom';
import styles from './classroom.module.css';

const TITLES: Record<PanelKey, string> = { transcript: '原文', explanation: 'AI 解释', qa: '问答' };
const STEP_PX = 24;

/** Width of the element itself (the classroom container), not the viewport. */
function useContainerWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    const observer = new ResizeObserver(entries => { for (const entry of entries) setWidth(Math.floor(entry.contentRect.width)); });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/**
 * UI-CLASS-02/03/12: three columns ≥1160 px, two columns 772–1159 px (transcript plus tabs), one
 * column below 772 px (tabs); minimum widths win over the PRD's 720 px initial value (CF-14). Panels stay mounted while hidden so drafts and requests survive
 * layout changes, collapsing and focus mode. Separators work with pointer, keyboard and touch; a
 * double click or the reset button restores the initial ratios. Only layout is remembered.
 */
export function Workspace({ panels, focus }: { panels: Record<PanelKey, ReactNode>; focus: boolean }) {
  const shell = useClassroomShell();
  const { ref, width } = useContainerWidth();
  const [prefs, setPrefs] = useState<LayoutPrefs>(() => parseLayout(shell.layoutStorage.read()));
  const [tab, setTab] = useState<PanelKey>('transcript');
  const [auxTab, setAuxTab] = useState<'explanation' | 'qa'>('explanation');
  useEffect(() => { shell.layoutStorage.write(JSON.stringify(prefs)); }, [prefs, shell.layoutStorage]);
  const measured = width > 0;
  const mode: LayoutMode = focus ? 'one' : measured ? layoutMode(width) : 'one';
  const widths = mode === 'three' ? columnWidths(width, prefs) : null;
  const drag = useRef<{ left: PanelKey; right: PanelKey; x: number; start: LayoutPrefs } | null>(null);

  const separator = (left: PanelKey, right: PanelKey) => {
    const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
      const delta = event.key === 'ArrowLeft' ? -STEP_PX : event.key === 'ArrowRight' ? STEP_PX : event.key === 'Home' ? -width : event.key === 'End' ? width : 0;
      if (delta) { event.preventDefault(); setPrefs(current => moveSeparator(width, current, left, right, delta)); }
      if (event.key === 'Enter') { event.preventDefault(); setPrefs(current => ({ ...current, ratios: defaultLayout().ratios })); }
    };
    const onDown = (event: PointerEvent<HTMLButtonElement>) => { event.currentTarget.setPointerCapture(event.pointerId); drag.current = { left, right, x: event.clientX, start: prefs }; };
    const onMove = (event: PointerEvent<HTMLButtonElement>) => { const active = drag.current; if (active) setPrefs(moveSeparator(width, active.start, active.left, active.right, event.clientX - active.x)); };
    const onUp = () => { drag.current = null; };
    const now = widths ? widths[left] : 0;
    return <button type="button" role="separator" aria-orientation="vertical" aria-label={`调整${TITLES[left]}与${TITLES[right]}的宽度`} aria-valuenow={Math.round(now)} aria-valuemin={0} aria-valuemax={Math.round(width)}
      className={styles.separator} onKeyDown={onKey} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
      onDoubleClick={() => setPrefs(current => ({ ...current, ratios: defaultLayout().ratios }))} title="拖动或用方向键调整，双击或按 Enter 重置" />;
  };
  const collapse = (key: 'explanation' | 'qa', value: boolean) => setPrefs(current => ({ ...current, collapsed: { ...current.collapsed, [key]: value } }));

  const section = (key: PanelKey, visible: boolean, style?: CSSProperties, extra?: ReactNode) =>
    <section key={key} className={`${styles.panel} ${styles.column}`} hidden={!visible} style={style} aria-labelledby={`panel-${key}`} data-panel={key}>
      <div className={styles.panelHead}><h2 id={`panel-${key}`}>{TITLES[key]}</h2>{extra}</div>
      {panels[key]}
    </section>;

  let body: ReactNode;
  if (mode === 'three' && widths) {
    const { collapsed } = prefs;
    const visible = (['transcript', 'explanation', 'qa'] as const).filter(key => key === 'transcript' || !collapsed[key]);
    const columns: ReactNode[] = [];
    visible.forEach((key, index) => {
      if (index > 0) columns.push(<span key={`sep-${key}`} className={styles.row}>{separator(visible[index - 1]!, key)}</span>);
      columns.push(section(key, true, { flex: `0 0 ${widths[key]}px`, width: widths[key] }, key !== 'transcript' && <button type="button" className={styles.buttonGhost} onClick={() => collapse(key, true)}>折叠</button>));
    });
    const hidden = (['explanation', 'qa'] as const).filter(key => collapsed[key]);
    body = <>
      <div className={styles.columns} data-testid="classroom-columns">{columns}{hidden.map(key => section(key, false))}</div>
      <div className={styles.row} style={{ marginTop: '.5rem' }}>
        {hidden.map(key => <button key={key} type="button" className={styles.buttonSecondary} onClick={() => collapse(key, false)}>展开{TITLES[key]}</button>)}
        <button type="button" className={styles.buttonGhost} onClick={() => setPrefs(defaultLayout())}>重置布局</button>
      </div>
    </>;
  } else if (mode === 'two') {
    body = <div className={styles.columns} data-testid="classroom-columns" style={{ gap: 12 }}>
      {section('transcript', true, { flex: '1 1 58%', minWidth: MIN_WIDTH.transcript })}
      <div className={styles.column} style={{ flex: '1 1 42%', minWidth: Math.max(MIN_WIDTH.explanation, MIN_WIDTH.qa) }}>
        <div className={styles.tabs} role="tablist" aria-label="辅助面板">{(['explanation', 'qa'] as const).map(key => <button key={key} type="button" role="tab" aria-selected={auxTab === key} className={`${styles.tab} ${auxTab === key ? styles.tabActive : ''}`} onClick={() => setAuxTab(key)}>{TITLES[key]}</button>)}</div>
        {section('explanation', auxTab === 'explanation')}
        {section('qa', auxTab === 'qa')}
      </div>
    </div>;
  } else {
    // Focus mode keeps only the transcript visible; the other panels stay mounted but hidden.
    const current = focus ? 'transcript' : tab;
    body = <div data-testid="classroom-columns">
      {!focus && <div className={styles.tabs} role="tablist" aria-label="课堂面板">{(['transcript', 'explanation', 'qa'] as const).map(key => <button key={key} type="button" role="tab" aria-selected={tab === key} className={`${styles.tab} ${tab === key ? styles.tabActive : ''}`} onClick={() => setTab(key)}>{TITLES[key]}</button>)}</div>}
      {(['transcript', 'explanation', 'qa'] as const).map(key => section(key, current === key))}
    </div>;
  }
  return <div ref={ref} className={`${styles.workspace} ${focus ? styles.focusTranscript : ''}`} data-layout={mode} data-container-width={width}>{body}</div>;
}
