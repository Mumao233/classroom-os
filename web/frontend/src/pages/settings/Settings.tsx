import { useEffect, useState } from 'react';
import { Navigate, NavLink, useParams } from 'react-router';
import { useMutation } from '@tanstack/react-query';
import type { components } from '../../generated/openapi';
import { describeError, isConflict, useAppData, useDevices, useDeviceSessions, useInvalidateAccount, useMe, useMeta, usePreferences, useQuota } from '../../features/data';
import { formatDateTime } from '../../domain/dashboard';
import { Badge, DataSourceNote, Empty, Loading, OfflineBanner, PageHeading, QueryError } from '../common';
import shell from '../../app/Shell.module.css';
import styles from '../pages.module.css';

type S = components['schemas'];
declare const __BUILD_ID__: string;
export type Theme = 'system' | 'day' | 'night';
export type Effects = 'normal' | 'reduced';
/** PRD p19 §5.5 sections, in the order of the frozen route table (02 §3). */
const sections = [
  { key: 'profile', label: '个人资料' }, { key: 'security', label: '账号安全与 MFA' }, { key: 'sessions', label: '设备会话' }, { key: 'trust', label: '可信设备' },
  { key: 'appearance', label: '主题与阅读' }, { key: 'ai', label: 'AI 使用偏好' }, { key: 'usage', label: '个人配额' }, { key: 'storage', label: '数据与缓存' },
  { key: 'data', label: '导入导出' }, { key: 'privacy', label: '删除与隐私' }, { key: 'about', label: '版本与反馈' },
] as const;

export function Settings({ theme, setTheme, effects, setEffects }: { theme: Theme; setTheme: (value: Theme) => void; effects: Effects; setEffects: (value: Effects) => void }) {
  const { section = 'appearance' } = useParams();
  if (!sections.some(item => item.key === section)) return <Navigate to="/settings/appearance" replace />;
  const current = sections.find(item => item.key === section)!;
  return <main id="main-content" className={shell.page} tabIndex={-1} data-testid="settings">
    <PageHeading title={current.label} detail="设置" focusKey={section} />
    <div className={styles.settingsLayout}>
      <nav className={styles.settingsNav} aria-label="设置分区">{sections.map(item => <NavLink key={item.key} to={`/settings/${item.key}`}>{item.label}</NavLink>)}</nav>
      <div className={styles.stack}>
        <OfflineBanner scope="设置只读。" /><DataSourceNote />
        {section === 'profile' && <Profile />}
        {section === 'security' && <Security />}
        {section === 'sessions' && <Sessions />}
        {section === 'trust' && <Trust />}
        {section === 'appearance' && <Appearance theme={theme} setTheme={setTheme} effects={effects} setEffects={setEffects} />}
        {section === 'ai' && <AiPreferences />}
        {section === 'usage' && <Usage />}
        {section === 'storage' && <NotYet title="数据与缓存" pr="PR-08a" text="学习缓存、未同步草稿和待上传音频将分三类显示字节数、最早风险和清理影响。可信设备加密缓存尚未启用，本设备当前不保存任何私人学习缓存。" />}
        {section === 'data' && <NotYet title="导入导出" pr="PR-11b" text="课堂包导入、导出由服务端任务执行，尚未开放。不会在浏览器中执行包内内容。" />}
        {section === 'privacy' && <NotYet title="删除与隐私" pr="PR-11b" text="账号删除需先查看服务端计算的影响范围并重新验证身份，尚未在本页开放。课程与学期的删除在课程页中进行。" />}
        {section === 'about' && <About />}
      </div>
    </div>
  </main>;
}

function NotYet({ title, pr, text }: { title: string; pr: string; text: string }) {
  return <section className={styles.panel} data-testid="not-yet"><h2>{title}</h2><p className={`${styles.small} ${styles.muted}`}>{text}</p><Badge tone="info">尚未开放 · 计划于 {pr}</Badge></section>;
}

function Profile() {
  const me = useMe();
  if (me.isPending) return <Loading label="正在读取个人资料…" />;
  // Cached data survives a failed refetch so the mounted form keeps its draft.
  if (!me.data) return <QueryError error={me.error} retry={() => void me.refetch()} />;
  return <ProfileForm key={me.data.id} user={me.data} />;
}
function ProfileForm({ user }: { user: S['User'] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const [base, setBase] = useState(user.revision);
  const [values, setValues] = useState({ nickname: user.nickname, school: user.school, major: user.major });
  const [acked, setAcked] = useState<typeof values | null>(null);
  const saved = acked !== null && (['nickname', 'school', 'major'] as const).every(key => acked[key] === values[key].trim());
  const [latest, setLatest] = useState<number | null>(null);
  const save = useMutation({
    mutationFn: (revision: number) => api.updateProfile({ base_revision: revision, nickname: values.nickname.trim(), school: values.school.trim(), major: values.major.trim() }),
    onSuccess: async next => { setBase(next.revision); setAcked({ nickname: next.nickname, school: next.school, major: next.major }); setLatest(null); await invalidate(); },
    onError: async error => { setAcked(null); if (isConflict(error)) setLatest((await api.me()).revision); },
  });
  const field = (key: keyof typeof values, label: string, max: number) => <div className={styles.field}><label htmlFor={`profile-${key}`}>{label}</label><input id={`profile-${key}`} value={values[key]} maxLength={max} onChange={event => setValues({ ...values, [key]: event.target.value })} /></div>;
  return <form className={`${styles.panel} ${styles.form}`} onSubmit={event => { event.preventDefault(); save.mutate(base); }} aria-label="个人资料">
    <dl className={styles.dl}><dt>登录邮箱</dt><dd>{user.email}</dd><dt>角色</dt><dd>{user.role === 'admin' ? '管理员' : '学生'}</dd></dl>
    {field('nickname', '昵称', 100)}{field('school', '学校', 200)}{field('major', '专业', 200)}
    <p className={`${styles.small} ${styles.muted}`}>修改登录邮箱需要重新验证身份，在“账号安全与 MFA”中进行。</p>
    {save.isError && <p role="alert" className={styles.error}>{describeError(save.error)}{latest !== null && <> <button type="button" className={styles.buttonGhost} onClick={() => { setBase(latest); setLatest(null); save.mutate(latest); }}>以我的输入覆盖最新版本</button></>}</p>}
    {saved && <p role="status" className={styles.success}>已保存。</p>}
    <div><button type="submit" className={styles.button} disabled={!online || save.isPending}>{save.isPending ? '正在保存…' : '保存'}</button></div>
  </form>;
}

function Security() {
  const me = useMe();
  return <section className={styles.panel}><h2>账号安全与 MFA</h2>
    {me.isPending ? <Loading label="正在读取…" /> : me.isError ? <QueryError error={me.error} /> :
      <dl className={styles.dl}><dt>两步验证（MFA）</dt><dd>{me.data.mfa_enabled ? <Badge tone="success">已开启</Badge> : <Badge tone="warning">未开启</Badge>}</dd></dl>}
    <p className={`${styles.small} ${styles.muted}`}>改密、修改邮箱、开启或关闭 MFA、轮换恢复码和退出所有设备都需要重新验证身份，由账号安全流程（PR-03b）处理，本页不重复实现这些敏感操作。</p>
  </section>;
}

function Sessions() {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const sessions = useDeviceSessions();
  const revoke = useMutation({ mutationFn: (id: string) => api.revokeSession(id), onSuccess: () => invalidate() });
  if (sessions.isError) return <QueryError error={sessions.error} retry={() => void sessions.refetch()} />;
  if (sessions.isPending) return <Loading label="正在读取会话…" />;
  return <section className={styles.panel}><h2>设备会话</h2>
    {revoke.isError && <p role="alert" className={styles.error}>{describeError(revoke.error)}</p>}
    {sessions.data.items.length === 0 ? <Empty title="没有会话" description="当前没有可显示的登录会话。" /> :
      <ul className={styles.list}>{sessions.data.items.map(item => <li key={item.id} className={styles.listItem}><div><strong>{item.device_name || '未命名设备'}</strong>
        <p>最近活动 {formatDateTime(item.last_active_at)} · 到期 {formatDateTime(item.expires_at)}{item.revoked_at ? ` · 已撤销 ${formatDateTime(item.revoked_at)}` : ''}</p></div>
        {item.is_current ? <Badge tone="info">当前会话</Badge> : item.revoked_at ? <Badge>已撤销</Badge> :
          <button type="button" className={styles.buttonSecondary} disabled={!online || revoke.isPending} onClick={() => revoke.mutate(item.id)}>撤销</button>}</li>)}</ul>}
    <p className={`${styles.small} ${styles.muted}`}>退出当前会话请使用账号菜单中的退出登录，以便先处理未同步内容。</p>
  </section>;
}

function Trust() {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const devices = useDevices();
  const revoke = useMutation({ mutationFn: (id: string) => api.revokeDevice(id), onSuccess: () => invalidate() });
  if (devices.isError) return <QueryError error={devices.error} retry={() => void devices.refetch()} />;
  if (devices.isPending) return <Loading label="正在读取设备…" />;
  return <section className={styles.panel}><h2>可信设备</h2>
    <p className={`${styles.small} ${styles.muted}`}>可信设备可以保存加密学习缓存和草稿。设备信任流程与加密缓存尚未启用（PR-05a/08a），这里只显示与撤销。</p>
    {revoke.isError && <p role="alert" className={styles.error}>{describeError(revoke.error)}</p>}
    {devices.data.items.length === 0 ? <Empty title="还没有设备记录" description="登录过的设备会显示在这里。" /> :
      <ul className={styles.list}>{devices.data.items.map(item => <li key={item.id} className={styles.listItem}><div><strong>{item.device_name || '未命名设备'}</strong><p>{item.trusted ? '已信任' : '未信任'}{item.revoked_at ? ` · 已撤销 ${formatDateTime(item.revoked_at)}` : ''}</p></div>
        {item.trusted && !item.revoked_at && <button type="button" className={styles.buttonSecondary} disabled={!online || revoke.isPending} onClick={() => revoke.mutate(item.id)}>撤销信任</button>}</li>)}</ul>}
  </section>;
}

/** Theme and effects apply immediately on this device; reading size is an account preference. */
function Appearance({ theme, setTheme, effects, setEffects }: { theme: Theme; setTheme: (value: Theme) => void; effects: Effects; setEffects: (value: Effects) => void }) {
  const preferences = usePreferences();
  return <>
    <section className={styles.panel}><h2>主题</h2><p className={`${styles.small} ${styles.muted}`}>跟随系统，或手动选择外观。切换主题不会中断正在进行的课堂。</p>
      <div className={styles.choices} role="radiogroup" aria-label="主题">{([{ value: 'system', title: '跟随系统', desc: '随设备外观变化' }, { value: 'day', title: 'Aurora Day', desc: '明亮而清晰' }, { value: 'night', title: 'Nebula Night', desc: '沉静的夜间阅读' }] as const).map(item =>
        <label className={styles.choice} key={item.value}><input type="radio" name="theme" value={item.value} checked={theme === item.value} onChange={() => setTheme(item.value)} /><span><strong>{item.title}</strong><br /><small className={styles.muted}>{item.desc}</small></span></label>)}</div></section>
    <section className={styles.panel}><h2>视觉效果</h2><p className={`${styles.small} ${styles.muted}`}>减少透明、模糊与动画效果，同时保留所有功能。</p>
      <label className={styles.choice}><input type="checkbox" checked={effects === 'reduced'} onChange={event => setEffects(event.target.checked ? 'reduced' : 'normal')} /> 低特效模式</label></section>
    {preferences.data ? <ReadingForm preferences={preferences.data} /> : preferences.isPending ? <Loading label="正在读取阅读偏好…" /> : <QueryError error={preferences.error} />}
  </>;
}
function ReadingForm({ preferences }: { preferences: S['Preferences'] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const [size, setSize] = useState(preferences.reading_font_px);
  const cas = usePreferenceBase(preferences, size !== preferences.reading_font_px);
  const save = useMutation({ mutationFn: (revision: number) => api.updatePreferences({ base_revision: revision, reading_font_px: size }), onSuccess: async next => { cas.acknowledge(next); await invalidate(); }, onError: cas.onError });
  return <section className={styles.panel}><h2>阅读字号</h2>
    <div className={styles.field}><label htmlFor="reading-size">正文与字幕字号：{size}px</label><input id="reading-size" type="range" min={14} max={32} step={1} value={size} onChange={event => setSize(Number(event.target.value))} /></div>
    <p style={{ fontSize: size, lineHeight: 1.7 }} className={styles.wrap}>示例：特征向量在线性变换下只被拉伸或压缩。</p>
    {cas.latest ? <PreferenceConflict onKeep={() => save.mutate(cas.takeLatest())} onDiscard={() => { setSize(cas.latest!.reading_font_px); cas.takeLatest(); save.reset(); }} />
      : save.isError && <p role="alert" className={styles.error}>{describeError(save.error)}</p>}
    <button type="button" className={styles.button} disabled={!online || save.isPending || size === preferences.reading_font_px} onClick={() => save.mutate(cas.base)}>保存字号</button>
  </section>;
}

/**
 * CAS base for a preference form that stays mounted: an unedited form follows the latest
 * revision; an edited one keeps its base so another device's change surfaces as a conflict
 * with an explicit keep-mine / load-latest choice instead of silently dropping the draft.
 */
function usePreferenceBase(preferences: S['Preferences'], dirty: boolean) {
  const { api } = useAppData();
  const [base, setBase] = useState(preferences.revision);
  const [latest, setLatest] = useState<S['Preferences'] | null>(null);
  useEffect(() => { if (!dirty && !latest) setBase(preferences.revision); }, [dirty, latest, preferences.revision]);
  return {
    base, latest,
    acknowledge: (next: S['Preferences']) => { setBase(next.revision); setLatest(null); },
    onError: async (error: unknown) => { if (isConflict(error)) setLatest(await api.preferences()); },
    takeLatest: () => { const revision = latest?.revision ?? base; setBase(revision); setLatest(null); return revision; },
  };
}
function PreferenceConflict({ onKeep, onDiscard }: { onKeep: () => void; onDiscard: () => void }) {
  return <div className={`${styles.banner} ${styles.bannerWarning}`} role="alert" data-testid="conflict-panel"><div className={styles.stack}>
    <strong>偏好已在其他设备更新，你的修改仍保留。</strong>
    <div className={styles.row}><button type="button" className={styles.button} onClick={onKeep}>以我的修改覆盖最新版本</button>
      <button type="button" className={styles.buttonSecondary} onClick={onDiscard}>放弃我的修改，载入最新版本</button></div></div></div>;
}

/** CF-15: allowed preferences only — no platform key, provider URL, model or BYOK fields. */
function AiPreferences() {
  const preferences = usePreferences();
  if (preferences.isPending) return <Loading label="正在读取 AI 偏好…" />;
  if (!preferences.data) return <QueryError error={preferences.error} retry={() => void preferences.refetch()} />;
  return <AiForm preferences={preferences.data} />;
}
function AiForm({ preferences }: { preferences: S['Preferences'] }) {
  const { api, online } = useAppData();
  const invalidate = useInvalidateAccount();
  const [language, setLanguage] = useState(preferences.explanation_language);
  const [automatic, setAutomatic] = useState(preferences.automatic_explanation);
  const changed = language !== preferences.explanation_language || automatic !== preferences.automatic_explanation;
  const cas = usePreferenceBase(preferences, changed);
  const save = useMutation({ mutationFn: (revision: number) => api.updatePreferences({ base_revision: revision, explanation_language: language, automatic_explanation: automatic }), onSuccess: async next => { cas.acknowledge(next); await invalidate(); }, onError: cas.onError });
  return <form className={`${styles.panel} ${styles.form}`} onSubmit={event => { event.preventDefault(); save.mutate(cas.base); }} aria-label="AI 使用偏好" data-testid="ai-preferences">
    <div className={styles.field}><label htmlFor="ai-language">解释语言</label><select id="ai-language" value={language} onChange={event => setLanguage(event.target.value as S['Preferences']['explanation_language'])}><option value="auto">跟随课堂</option><option value="zh">中文</option><option value="en">英文</option></select></div>
    <label className={styles.choice}><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} aria-describedby="ai-auto-hint" /> <span>课堂中自动解释<br /><small id="ai-auto-hint" className={styles.muted}>默认关闭。开启后每次自动解释都计入个人互动额度，额度不足时停止。</small></span></label>
    <p className={`${styles.small} ${styles.muted}`}>模型服务和密钥由平台在服务端统一管理，学生账号不配置模型地址或密钥，也不支持自带 API Key。</p>
    {cas.latest ? <PreferenceConflict onKeep={() => save.mutate(cas.takeLatest())} onDiscard={() => { setLanguage(cas.latest!.explanation_language); setAutomatic(cas.latest!.automatic_explanation); cas.takeLatest(); save.reset(); }} />
      : save.isError && <p role="alert" className={styles.error}>{describeError(save.error)}</p>}
    <div><button type="submit" className={styles.button} disabled={!online || save.isPending || !changed}>保存</button></div>
  </form>;
}

const bucketLabels: Record<S['QuotaBucket']['kind'], [string, 'ms' | 'count']> = {
  asr_month_ms: ['本月转写时长', 'ms'], asr_day_ms: ['今日转写时长', 'ms'], retranscribe_month_ms: ['本月重新转写时长', 'ms'], interactive_month_count: ['本月问答与解释次数', 'count'], summary_month_count: ['本月总结次数', 'count'],
};
const billingLabels: Record<S['QuotaView']['platform_billing_state'], string> = { normal: '正常', warning60: '平台用量已过 60%', warning80: '平台用量已过 80%', blocked100: '平台预算已用尽，新的收费任务暂停', paused: '收费服务已暂停' };
const amount = (value: number, unit: 'ms' | 'count') => unit === 'ms' ? `${Math.round(value / 60000)} 分钟` : `${value} 次`;
function Usage() {
  const quota = useQuota();
  if (quota.isError) return <QueryError error={quota.error} retry={() => void quota.refetch()} />;
  if (quota.isPending) return <Loading label="正在读取额度…" />;
  const view = quota.data;
  return <section className={styles.panel} data-testid="quota"><h2>个人配额</h2>
    <p className={`${styles.small} ${styles.muted}`}>平台状态：{billingLabels[view.platform_billing_state]} · 转写{view.asr_enabled ? '可用' : '暂停'} · AI {view.ai_enabled ? '可用' : '暂停'}。额度不足或暂停时，已有资料仍可阅读。</p>
    {view.buckets.length === 0 ? <Empty title="暂无额度信息" description="服务端没有返回额度桶。" /> :
      <ul className={styles.list}>{view.buckets.map(bucket => { const [label, unit] = bucketLabels[bucket.kind]; const used = bucket.limit - bucket.remaining; return <li key={bucket.kind} className={styles.listItem}><div>
        <strong>{label}</strong><p>剩余 {amount(bucket.remaining, unit)} / 共 {amount(bucket.limit, unit)}{bucket.unknown > 0 ? ` · ${amount(bucket.unknown, unit)} 结果未知，待核对` : ''}{bucket.reserved + bucket.submitted > 0 ? ` · ${amount(bucket.reserved + bucket.submitted, unit)} 处理中` : ''}</p>
        <div className={styles.meter} role="img" aria-label={`${label}已用 ${bucket.limit ? Math.round((used / bucket.limit) * 100) : 0}%`}><span style={{ width: `${bucket.limit ? Math.min(100, (used / bucket.limit) * 100) : 0}%` }} /></div>
        <p>周期 {formatDateTime(bucket.period_start)} – {formatDateTime(bucket.period_end)}</p></div></li>; })}</ul>}
  </section>;
}

function About() {
  const meta = useMeta();
  return <section className={styles.panel}><h2>版本与反馈</h2>
    {meta.isError ? <QueryError error={meta.error} /> : meta.isPending ? <Loading label="正在读取版本…" /> :
      <dl className={styles.dl}><dt>前端构建</dt><dd>{__BUILD_ID__}</dd><dt>服务版本</dt><dd>{meta.data.product_version}</dd><dt>接口契约</dt><dd>{meta.data.contract_version}</dd><dt>最低客户端</dt><dd>{meta.data.minimum_client_build}</dd></dl>}
    <p className={`${styles.small} ${styles.muted}`}>问题反馈入口（PR-11b）尚未开放；反馈默认不会附带正文、密钥或整课音频。</p>
  </section>;
}
