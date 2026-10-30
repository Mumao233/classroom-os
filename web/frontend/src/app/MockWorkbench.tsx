import { useEffect, useMemo, useState } from 'react';
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { IconBell, IconBook2, IconBooks, IconCards, IconChalkboard, IconChevronRight, IconCircleDot, IconHeadphones, IconLayoutDashboard, IconPlayerStop, IconSearch, IconSettings, IconWaveSine } from '@tabler/icons-react';
import { Dashboard } from '../pages/Dashboard';
import { Placeholder } from '../pages/Placeholder';
import { Catalog } from '../ui';
import { useMockRuntime } from '../mock/runtime';
import { setMockAccount } from '../mock/start';
import { eventScripts, type EventScriptName } from '../mock/event-scripts';
import { createApiClient } from '../api/client';
import styles from './Shell.module.css';

type Theme = 'system' | 'day' | 'night';
type Effects = 'normal' | 'reduced';
const nav = [
  { to: '/dashboard', text: '首页', Icon: IconLayoutDashboard, DesktopIcon: IconLayoutDashboard },
  { to: '/courses', text: '课程', Icon: IconBook2, DesktopIcon: IconBooks },
  { to: '/classrooms', text: '课堂', Icon: IconHeadphones, DesktopIcon: IconChalkboard },
  { to: '/review', text: '复习', Icon: IconCards, DesktopIcon: IconCards },
  { to: '/settings/appearance', text: '设置', Icon: IconSettings, DesktopIcon: IconSettings },
];
const scenarioNames = ['首次使用 · 空课程', '合成的最近记录', '模拟课堂跨页面', '模拟上传失败', '离线与暂存状态', 'AI 结果未知', '候选与个人稿', '版本冲突', '来源不可用', '额度限制', '账号切换', '组件状态'];

function Appearance({ theme, setTheme, effects, setEffects }: {theme: Theme; setTheme: (value: Theme) => void; effects: Effects; setEffects: (value: Effects) => void}) {
  return <main id="main-content" className={styles.page} tabIndex={-1}><div className={styles.pageHeading}><div><p className={styles.eyebrow}>PERSONALIZE YOUR SPACE</p><h1>外观设置</h1><p>选择适合自己的阅读环境。</p></div></div>
    <section className={`${styles.panel} ${styles.settingsPanel}`}><h2>主题</h2><p>跟随系统，或手动选择你喜欢的外观。</p><div className={styles.themeChoices}>{([{value:'system', title:'跟随系统', desc:'随设备外观变化'}, {value:'day', title:'Aurora Day', desc:'明亮而清晰'}, {value:'night', title:'Nebula Night', desc:'沉静的夜间阅读'}] as const).map(item => <label className={styles.themeChoice} key={item.value}><input type="radio" name="theme" value={item.value} checked={theme===item.value} onChange={() => setTheme(item.value)}/><span><strong>{item.title}</strong><small>{item.desc}</small></span></label>)}</div></section>
    <section className={`${styles.panel} ${styles.settingsPanel}`}><h2>视觉效果</h2><p>减少透明、模糊与动画效果，同时保留所有功能。</p><label className={styles.effectChoice}><input type="checkbox" checked={effects==='reduced'} onChange={event => setEffects(event.target.checked ? 'reduced' : 'normal')}/> 低特效模式</label></section>
  </main>;
}

export default function MockWorkbench() {
  const runtime = useMockRuntime();
  const scopeEpoch = runtime.state.epoch;
  // Each authenticated epoch owns its client; account teardown cannot reuse an old cache.
  const queryClient = useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, meta: { mockScopeEpoch: scopeEpoch } } } }), [scopeEpoch]);
  useEffect(() => () => { void queryClient.cancelQueries(); queryClient.clear(); }, [queryClient]);
  return <QueryClientProvider key={scopeEpoch} client={queryClient}><MockWorkbenchContent runtime={runtime} /></QueryClientProvider>;
}

function MockWorkbenchContent({ runtime }: { runtime: ReturnType<typeof useMockRuntime> }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [theme, setTheme] = useState<Theme>(() => { const stored = localStorage.getItem('aurora-theme'); return stored === 'day' || stored === 'night' ? stored : 'system'; });
  const [effects, setEffects] = useState<Effects>(() => localStorage.getItem('aurora-effects') === 'reduced' ? 'reduced' : 'normal');
  const [systemNight, setSystemNight] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);
  const [eventScript, setEventScript] = useState<EventScriptName>('transcript');
  const [eventStep, setEventStep] = useState(0);
  const { scenario, active, account, epoch, flows } = runtime.state;
  const { acceptAccountResponse, isCurrent } = runtime;
  const api = useMemo(() => createApiClient({ epoch, isCurrent }), [epoch, isCurrent]);
  useEffect(() => { const media = matchMedia('(prefers-color-scheme: dark)'); const update = () => setSystemNight(media.matches); media.addEventListener('change', update); return () => media.removeEventListener('change', update); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme === 'system' ? (systemNight ? 'night' : 'day') : theme; localStorage.setItem('aurora-theme', theme); }, [theme, systemNight]);
  useEffect(() => { document.documentElement.dataset.effects = effects; localStorage.setItem('aurora-effects', effects); }, [effects]);
  // An unsolicited revocation also clears the mock transport and query cache.
  useEffect(() => { if (!account) { setMockAccount(null); void queryClient.cancelQueries(); queryClient.clear(); } }, [account, epoch, queryClient]);
  const coursesQuery = useQuery({
    queryKey: ['account', account, 'courses', epoch],
    enabled: account !== null,
    queryFn: async ({ signal }) => {
      const requestEpoch = epoch;
      if (!isCurrent(requestEpoch)) throw new Error('stale account request');
      const data = await api.getCourses(signal);
      if (!isCurrent(requestEpoch) || !acceptAccountResponse(requestEpoch, data)) throw new Error('stale account response');
      return data;
    },
  });
  const apiStatus = !account ? 'revoked' : coursesQuery.isPending ? 'loading' : coursesQuery.isError ? 'error' : 'ready';
  const start = () => { if (active || runtime.start()) navigate('/classrooms/mock/live'); };
  // Invalidate the scope before cancelling requests so an uncancellable late reply is rejected too.
  const clearQueries = () => { void queryClient.cancelQueries(); queryClient.clear(); };
  const switchAccount = () => { runtime.switchAccount(); setMockAccount(runtime.getState().account); clearQueries(); navigate('/dashboard'); };
  const revokeAccount = () => { runtime.revokeAccount(); setMockAccount(null); clearQueries(); navigate('/dashboard'); };
  const loginAccount = (next: 'A' | 'B') => { runtime.loginAccount(next); setMockAccount(next); clearQueries(); navigate('/dashboard'); };
  const runEventScript = () => {
    runtime.runEventScript(eventScript);
    setEventStep(0);
  };
  const runEventStep = () => {
    runtime.runEventStep(eventScript, eventStep);
    setEventStep((eventStep + 1) % eventScripts[eventScript].length);
  };
  const failure = flows.upload === 'failed';
  const unknownRiskCount = Object.values(runtime.state.tasks).filter(task => task.mayHaveCharged).length;
  const syncLabel = flows.sync === 'local_only' ? '仅模拟本地保存' : flows.sync === 'failed' ? '未安全保存' : flows.sync === 'synced' ? '已同步' : flows.sync;
  return <div className={styles.app}>
    <a className={styles.skipLink} href="#main-content">跳至主要内容</a>
    <header className={styles.header}><NavLink to="/dashboard" className={styles.brand} data-testid="brand" aria-label="Classroom OS 首页"><span className={styles.brandMark}/></NavLink>
      <nav className={styles.desktopNav} data-testid="desktop-nav" aria-label="主导航">{nav.map(({to,text,DesktopIcon}) => <NavLink key={to} to={to === '/classrooms' && active ? '/classrooms/mock/live' : to} aria-label={text} title={text} className={({isActive}) => `${styles.navItem} ${isActive ? styles.navActive : ''}`}><DesktopIcon size={18}/></NavLink>)}</nav>
      <div className={styles.headerActions} data-testid="header-actions"><LinkWithIcon to="/search" label="搜索"><IconSearch size={20}/></LinkWithIcon><LinkWithIcon to="/notifications" label="通知"><IconBell size={20}/></LinkWithIcon><NavLink className={styles.avatar} aria-label="外观设置" to="/settings/appearance">{account === 'A' ? '林' : account === 'B' ? '陈' : '·'}</NavLink></div>
    </header>
    <div className={styles.devBanner}><span className={styles.devDot}/> 开发预览 · 合成数据 <span className={styles.devDivider}>/</span> <span>{apiStatus === 'ready' ? 'Mock API 已连接' : apiStatus === 'loading' ? 'Mock API 连接中' : apiStatus === 'revoked' ? '模拟会话已撤销' : 'Mock API 不可用'}</span><NavLink to="/__dev/catalog">组件目录 ↗</NavLink></div>
    {!account ? <main id="main-content" className={styles.page}><section className={`${styles.panel} ${styles.empty}`}><h1>模拟会话已撤销</h1><p>已清除模拟账号内容、事件订阅和采集状态。</p><button type="button" className={styles.primaryButton} onClick={() => loginAccount('A')}>进入模拟账号 A</button><button type="button" className={styles.secondaryButton} onClick={() => loginAccount('B')}>进入模拟账号 B</button></section></main> :
    <Routes><Route path="/" element={<Navigate to="/dashboard" replace/>}/><Route path="/dashboard" element={coursesQuery.data ? <Dashboard empty={scenario === '01' || coursesQuery.data.items.length === 0} account={account} coursePage={coursesQuery.data} classroomDeleted={runtime.state.eventProjection.classroomDeleted} onStart={start} active={active}/> : <main id="main-content" className={styles.page} role="status">{coursesQuery.isError ? '模拟课程暂不可用' : '正在读取模拟课程…'}</main>}/><Route path="/__dev/catalog" element={<main id="main-content" className={styles.page}><Catalog theme={theme === 'system' ? (systemNight ? 'night' : 'day') : theme} onThemeChange={setTheme} onStopCapture={active ? runtime.stop : undefined}/></main>}/><Route path="/settings/*" element={<Appearance theme={theme} setTheme={setTheme} effects={effects} setEffects={setEffects}/>}/><Route path="/courses/*" element={<Placeholder title="课程" detail="课程与课堂资料将在这里整理。"/>}/><Route path="/classrooms/*" element={<Placeholder title={active ? '模拟课堂进行中' : '课堂'} detail={active ? '这是跨页面保留的模拟状态；没有启动真实采集。' : '课堂入口与准备流程将在后续实现。'}/>}/><Route path="/review/*" element={scenario === '10' ? <main id="main-content" className={styles.page}><section className={styles.panel}><h1>模拟只读资料</h1><p>合成的学习资料示例：额度限制时，已有资料仍可阅读。</p><p>复习页面仍在开发，本示例不调用模型，也不会发起收费请求。</p></section></main> : <Placeholder title="复习" detail="从课堂记录继续学习。"/>}/><Route path="/tasks" element={<Placeholder title="待处理" detail="查看需要关注的任务。"/>}/><Route path="/notifications" element={<Placeholder title="通知" detail="查看学习动态和提醒。"/>}/><Route path="/search" element={<Placeholder title="搜索" detail="搜索功能将在后续实现。"/>}/><Route path="*" element={<Placeholder title="页面不可用" detail="该页面不存在或尚未开放。"/>}/></Routes>}
    {account && <aside className={styles.scenarioPanel} aria-label="开发模拟控制"><label htmlFor="scenario">模拟场景</label><select id="scenario" value={scenario} onChange={event => runtime.chooseScenario(event.target.value)}>{scenarioNames.map((name,index) => { const number = String(index + 1).padStart(2,'0'); return <option key={number} value={number}>MOCK-{number} · {name}</option>; })}</select><button onClick={switchAccount} type="button">切换模拟账号 {account} <IconChevronRight size={14}/></button><button onClick={revokeAccount} type="button">撤销模拟会话</button><ScenarioNotice scenario={scenario}/><ScenarioView key={scenario} scenario={scenario} localWrite={runtime.state.localWriteResult} localRecord={runtime.state.mockLocalRecord} writeLocal={runtime.simulateLocalWrite} reconcileLocal={runtime.reconcileLocalWrite} lockVault={runtime.lockMockVault} unlockVault={runtime.unlockMockVault} stopCapture={runtime.stop} checkRetry={runtime.requestAiRetry}/>
      <div className={styles.scenarioDetail}><label htmlFor="event-script">模拟事件脚本</label>{' '}<select id="event-script" value={eventScript} onChange={event => { setEventScript(event.target.value as EventScriptName); setEventStep(0); }}><option value="transcript">临时与正式转写</option><option value="upload">帧确认与持久上传</option><option value="taskUnknown">AI 结果未知</option><option value="deletion">原音撤权</option><option value="classroomDeletion">课堂撤权</option><option value="leaseLoss">采集租约失效</option><option value="revocation">会话撤销</option></select>{' '}<button type="button" onClick={runEventScript}>运行模拟事件</button>{' '}<button type="button" onClick={runEventStep}>模拟下一步事件</button>{runtime.state.eventProjection.leaseLost && !runtime.state.eventProjection.classroomDeleted && <button type="button" onClick={() => runtime.acquireMockLease()}>重新获取模拟租约</button>}<div role="region" aria-label="模拟事件结果" data-port-capture={runtime.state.eventProjection.portCaptureState} data-port-upload-pending={String(runtime.state.eventProjection.uploadPending)}><p>正式片段：{runtime.state.eventProjection.finalText ?? '无'}；临时片段：{runtime.state.eventProjection.partial ?? '无'}</p><p>帧确认：{runtime.state.eventProjection.frameReceived ? '已收到' : '未收到'}；持久上传：{runtime.state.eventProjection.chunkDurable ? '已确认' : '未确认'}</p><p>当前课堂 AI 结果：{flows.ai === 'unknown' ? '未知，可能已收费' : flows.ai}；原音撤权：{runtime.state.eventProjection.audioDeleted ? '已撤权' : '无'}；课堂撤权：{runtime.state.eventProjection.classroomDeleted ? '已撤权' : '无'}；采集租约：{runtime.state.eventProjection.leaseLost ? '已失效' : '正常'}</p><p>全部课堂的未知收费风险：{unknownRiskCount} 项{unknownRiskCount > 0 ? '，请先核对，不能自动重试' : ''}</p></div></div>
      <section className={styles.scenarioDetail} aria-label="模拟上传历史"><strong>模拟上传历史</strong>{runtime.state.uploadHistory.length === 0 ? <p>暂无模拟分片。</p> : <ol>{runtime.state.uploadHistory.map(item => <li key={`${item.captureEpochId}:${item.chunkId}:${item.seq}`} data-chunk-id={item.chunkId} data-capture-epoch={item.captureEpochId} data-status={item.status}><span>分片 {item.seq} · {item.status === 'durable' ? '已持久确认' : item.canAccept ? '待持久确认' : '已失效，无法确认'} · 帧确认{item.frameReceived ? '已收到' : '未收到'}{item.chunkId === runtime.state.captureScope?.chunkId ? ' · 当前采集' : ' · 历史采集'}</span>{item.status === 'pending' && item.canAccept && item.chunkId !== runtime.state.captureScope?.chunkId && <> <button type="button" onClick={() => runtime.runHistoricalDurable(item.chunkId)}>模拟迟到持久回执</button></>}</li>)}</ol>}</section>
    </aside>}
    {active && <div className={styles.captureBar} role="region" aria-label="模拟课堂状态"><button type="button" onClick={() => navigate('/classrooms/mock/live')} className={styles.captureTitle}><span className={styles.recordDot}/><span><strong>模拟录音中</strong><small>{account === 'A' ? '线性代数' : '模拟课堂 B'}</small></span><IconWaveSine className={styles.wave} size={24}/><strong>00:23:41</strong></button><div className={styles.flowList} aria-label="五流独立状态"><span>● 采集 · {flows.capture === 'recording' ? '录音中' : flows.capture}</span><span className={failure ? styles.flowWarning : ''}>{failure ? '！上传 · 失败' : flows.upload === 'durable' ? '✓ 上传 · 已确认' : '◌ 上传 · 待处理'}</span><span>◌ 转写 · {flows.asr === 'streaming' ? '进行中' : flows.asr === 'idle' ? '未开始' : flows.asr}</span><span>– AI · {flows.ai === 'idle' ? '无任务' : flows.ai === 'unknown' ? '结果未知' : flows.ai}</span><span className={flows.sync !== 'synced' ? styles.flowWarning : ''}>同步 · {syncLabel}</span></div><button type="button" className={styles.stopButton} onClick={runtime.stop}><IconPlayerStop size={17}/> 停止</button></div>}
    <nav className={styles.mobileNav} data-testid="mobile-nav" aria-label="移动主导航"><span className={styles.mobileIndicator} data-testid="mobile-indicator" aria-hidden="true"/>{nav.slice(0,4).map(({to,text,Icon}) => <NavLink key={to} to={to === '/classrooms' && active ? '/classrooms/mock/live' : to} className={({isActive}) => `${styles.mobileItem} ${isActive ? styles.mobileActive : ''}`}><Icon size={23}/><span>{text}</span></NavLink>)}</nav>
  </div>;
}

function LinkWithIcon({to,label,children}: {to:string; label:string; children:React.ReactNode}) { return <NavLink className={styles.headerIcon} to={to} aria-label={label}>{children}</NavLink>; }
function ScenarioNotice({scenario}: {scenario:string}) {
  const notices: Record<string,string> = {'04':'上传失败；其余状态独立显示。','05':'这是模拟暂存状态，不代表真实持久化。','06':'模拟结果未知，可能产生费用；应先核对，不自动重试。','07':'模拟候选尚未采用，个人稿保留。','08':'模拟冲突：两个版本均应保留。','09':'模拟原音已删除或图片缺失，不能播放。','10':'模拟额度受限，阅读入口仍可访问。','11':'切换模拟账号后，当前状态清除。','12':'请在组件目录查看状态示例。'};
  return notices[scenario] ? <p className={styles.scenarioNotice}><IconCircleDot size={15}/> {notices[scenario]}</p> : null;
}

function ScenarioView({scenario, localWrite, localRecord, writeLocal, reconcileLocal, lockVault, unlockVault, stopCapture, checkRetry}: {scenario:string; localWrite:'none'|'stored'|'write_failed'|'locked'|'quota'|'reconciled'; localRecord:string|null; writeLocal: (success:boolean) => boolean; reconcileLocal: () => boolean; lockVault: () => void; unlockVault: () => void; stopCapture: () => void; checkRetry: () => boolean}) {
  const [checked, setChecked] = useState(false);
  const [compare, setCompare] = useState(false);
  const [conflictAction, setConflictAction] = useState('');
  if (scenario === '05') return <div className={styles.scenarioDetail}><strong>离线写入模拟</strong><div><button type="button" onClick={() => writeLocal(true)}>模拟写入成功</button> <button type="button" onClick={() => writeLocal(false)}>模拟写入失败</button></div><div><button type="button" onClick={lockVault}>锁定模拟 Vault</button> <button type="button" onClick={unlockVault}>解锁模拟 Vault</button></div><p role="status">{localWrite === 'stored' ? '模拟本地保存成功，等待联网核对。' : localWrite === 'write_failed' ? '尚未安全保存，仅在当前页面。请处理后再离开。' : localWrite === 'locked' ? '模拟 Vault 已锁定，无法写入。' : localWrite === 'quota' ? '模拟 Vault 空间不足，写入被拒绝。' : localWrite === 'reconciled' ? '模拟联网核对完成；没有真实上传。' : '尚未安全保存。请选择模拟写入结果。'}</p><p>模拟 Vault 记录：{localWrite === 'locked' ? '已锁定，无法读取' : localRecord ?? '无'}</p>{localWrite === 'stored' && <button type="button" onClick={() => reconcileLocal()}>模拟联网核对</button>}{(localWrite === 'write_failed' || localWrite === 'locked' || localWrite === 'quota') && <button type="button" onClick={stopCapture}>停止模拟课堂</button>}</div>;
  if (scenario === '06') return <div className={styles.scenarioDetail}><strong>模拟 AI 结果未知</strong><p>请求可能已计费。请先核对，不会自动重试或发起真实请求。</p><button type="button" onClick={() => { checkRetry(); setChecked(true); }}>核对状态</button>{checked && <p>模拟核对：结果仍未知；未重新发送。</p>}</div>;
  if (scenario === '07' || scenario === '08') return <div className={styles.scenarioDetail}><strong>{scenario === '07' ? '候选尚未采用' : '模拟版本冲突'}</strong><p>个人稿与{scenario === '07' ? '新候选' : '云端版本'}均保留。</p><button type="button" onClick={() => setCompare(!compare)}>{compare ? '收起对比' : '查看对比'}</button>{compare && <div className={styles.compare}><p>个人稿：合成的原有笔记。</p><p>{scenario === '07' ? '候选' : '云端'}：合成的另一版本。</p></div>}{scenario === '08' && <><div><button type="button" onClick={() => setConflictAction('合并')}>模拟合并</button> <button type="button" onClick={() => setConflictAction('另存')}>模拟另存</button> <button type="button" onClick={() => setConflictAction('采用云端')}>模拟采用云端</button></div>{conflictAction && <p role="status">已选择模拟{conflictAction}路径；仅作预览，个人稿与云端版本仍保留，未保存任何更改。</p>}</>}</div>;
  if (scenario === '09') return <div className={styles.scenarioDetail}><strong>来源不可用</strong><p>模拟原音已删除；保留位置 00:12:30。图片离线缺失。</p><button type="button" disabled>播放原音不可用</button></div>;
  if (scenario === '10') return <div className={styles.scenarioDetail}><strong>模拟额度限制</strong><p>新收费任务已暂停；仍可阅读已有资料。</p><NavLink to="/review">前往复习入口</NavLink></div>;
  return null;
}
