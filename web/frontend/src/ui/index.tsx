import { useId, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import styles from './Aurora.module.css';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant };
export function Button({ variant = 'primary', className = '', type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={`${styles.button} ${styles[variant]} ${className}`} {...props} />;
}
export function IconButton({ className = '', variant = 'ghost', ...props }: ButtonProps & { 'aria-label': string }) {
  return <Button className={`${styles.iconButton} ${className}`} variant={variant} {...props} />;
}

export type InputProps = InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string };
export function Input({ label, hint, error, id, ...props }: InputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const descriptionId = `${inputId}-description`;
  return <div className={styles.field}>
    <label className={styles.label} htmlFor={inputId}>{label}</label>
    <input {...props} id={inputId} className={styles.input} aria-invalid={error ? true : undefined} aria-describedby={error || hint ? descriptionId : undefined} />
    {(error || hint) && <span id={descriptionId} className={error ? styles.error : styles.hint}>{error || hint}</span>}
  </div>;
}

export function Panel({ children, className = '', readable = false }: { children: ReactNode; className?: string; readable?: boolean }) {
  return <section className={`${styles.panel} ${readable ? styles.readable : ''} ${className}`}>{children}</section>;
}
export type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'recording' | 'offline' | 'info';
export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: StatusTone }) {
  const toneClass = tone === 'danger' ? styles.statusDanger : tone === 'neutral' ? '' : styles[tone];
  return <span className={`${styles.badge} ${toneClass}`}>{children}</span>;
}
export function FiveFlowStatus({ flows }: { flows: ReadonlyArray<{ name: string; label: string; tone?: StatusTone }> }) {
  return <div className={styles.flows} aria-label="五项独立状态">{flows.map((flow) => <StatusBadge key={flow.name} tone={flow.tone}>{flow.name}：{flow.label}</StatusBadge>)}</div>;
}
export function EmptyState({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <div className={styles.empty}><h3>{title}</h3><p>{description}</p>{children}</div>;
}

export function Dialog({ open, onOpenChange, title, description, children, onStopCapture }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode;
  /** Supply only while an active simulated capture must remain stoppable. */
  onStopCapture?: () => void;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    <DialogPrimitive.Portal><DialogPrimitive.Overlay className={styles.overlay} />
      <DialogPrimitive.Content ref={contentRef} className={styles.dialog}
        onOpenAutoFocus={() => { returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
        onCloseAutoFocus={(event) => {
          if (returnFocus.current?.isConnected) { event.preventDefault(); returnFocus.current.focus(); }
        }}>
        <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
        <DialogPrimitive.Description>{description}</DialogPrimitive.Description>
        <DialogPrimitive.Close asChild><IconButton className={styles.close} aria-label="关闭">×</IconButton></DialogPrimitive.Close>
        {onStopCapture && <div className={styles.dialogCritical}><Button variant="danger" onClick={() => { onStopCapture(); contentRef.current?.focus(); }}>停止模拟课堂</Button></div>}
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>;
}

/** Development-only component catalogue; it contains synthetic UI states. */
export function Catalog({ theme, onThemeChange, onStopCapture }: {
  theme: 'day' | 'night'; onThemeChange: (theme: 'day' | 'night') => void; onStopCapture?: () => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');
  const [submitted, setSubmitted] = useState(false);
  return <div className={styles.catalog}>
    <header className={styles.catalogHeader}>
      <div><p className={styles.catalogEyebrow}>AURORA · 组件目录</p><h1>基础组件与状态</h1><p>开发预览 · 所有内容均为模拟示例</p></div>
      <div className={styles.catalogActions} aria-label="预览主题">
        <Button variant={theme === 'day' ? 'primary' : 'secondary'} aria-pressed={theme === 'day'} onClick={() => onThemeChange('day')}>Aurora Day</Button>
        <Button variant={theme === 'night' ? 'primary' : 'secondary'} aria-pressed={theme === 'night'} onClick={() => onThemeChange('night')}>Nebula Night</Button>
      </div>
    </header>
    <div className={styles.catalogGrid}>
      <Panel><h2>操作</h2><p>键盘聚焦、悬停和不可用状态。</p><div className={styles.catalogActions}>
        <Button>主要操作</Button><Button variant="secondary">次要操作</Button><Button variant="ghost">轻量操作</Button><Button variant="danger">危险操作</Button><Button disabled>不可用</Button><IconButton aria-label="示例图标按钮">＋</IconButton>
      </div></Panel>
      <Panel><h2>输入与校验</h2><p>错误与输入关联，保留用户内容。</p><form className={styles.catalogForm} onSubmit={(event) => { event.preventDefault(); setSubmitted(true); }} noValidate>
        <Input label="课程名称" value={name} onChange={(event) => setName(event.target.value)} error={submitted && !name.trim() ? '请输入课程名称。' : undefined} hint="这是用于展示的模拟表单。" />
        <div><Button type="submit">检查输入</Button></div>
      </form></Panel>
      <Panel><h2>模态与焦点</h2><p>打开后可用 Tab 导航，Esc 关闭并返回触发点。</p><Button variant="secondary" onClick={() => setDialogOpen(true)}>打开示例对话框</Button>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen} title="确认示例操作" description="这是模拟对话框，不会提交或发送数据。" onStopCapture={onStopCapture}><div className={styles.catalogActions}><Button onClick={() => setDialogOpen(false)}>知道了</Button><Button variant="secondary" onClick={() => setDialogOpen(false)}>取消</Button></div></Dialog>
      </Panel>
      <Panel><h2>空状态</h2><EmptyState title="还没有课程" description="创建第一门课程后，最近记录会显示在这里。"><Button variant="secondary">创建课程（示例）</Button></EmptyState></Panel>
      <Panel><h2>加载与失败</h2><div className={styles.catalogStates}><p role="status"><StatusBadge tone="info">正在加载</StatusBadge> 正在读取模拟数据…</p><p><StatusBadge tone="danger">加载失败</StatusBadge> 内容暂时不可用，请稍后重试。</p><p><StatusBadge tone="neutral">不可用</StatusBadge> 此操作当前被禁用。</p></div></Panel>
      <Panel><h2>需要处理的状态</h2><div className={styles.catalogStates}><p><StatusBadge tone="warning">结果未知</StatusBadge> 可能已产生费用，请先核对状态。</p><p><StatusBadge tone="offline">离线</StatusBadge> 当前仅展示模拟可用内容。</p><p><StatusBadge tone="warning">版本冲突</StatusBadge> 本地和云端版本均已保留，请比较后处理。</p></div></Panel>
      <Panel readable><h2>长内容排版示例</h2><p>合成文字与公式，仅用于检查换行和局部滚动。</p>
        <h3 className={styles.longTitle} data-testid="long-course-name">线性代数与机器学习中的矩阵分解及其跨学科应用课程名称超长示例 LinearAlgebraAndMatrixFactorizationForInterdisciplinaryApplications</h3>
        <div className={styles.formula} role="region" aria-label="长公式示例" tabIndex={0}><code>∑(i=1…n) [ (xᵢ − μ)² + λ · (aᵢ₁b₁ + aᵢ₂b₂ + aᵢ₃b₃ + aᵢ₄b₄ + aᵢ₅b₅)² ] = α₁ + α₂ + α₃ + α₄ + α₅ + α₆ + α₇ + α₈</code></div>
      </Panel>
      <Panel><h2>五项独立状态</h2><p>每条状态单独呈现，故障不会被整体“完成”掩盖。</p><FiveFlowStatus flows={[
        { name: '采集', label: '模拟采集中', tone: 'recording' }, { name: '上传', label: '失败', tone: 'danger' }, { name: '转写', label: '进行中', tone: 'info' }, { name: 'AI', label: '无任务' }, { name: '同步', label: '已同步', tone: 'success' },
      ]} /></Panel>
    </div>
  </div>;
}
