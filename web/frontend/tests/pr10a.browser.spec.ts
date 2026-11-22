import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:19309';
const UNKNOWN_AI_CLASS = 'c0000000-0000-4000-8000-000000000006';
const DEMO_CLASS = '22222222-2222-4222-8222-222222222222';
const shots = resolve(process.cwd(), '../../outputs/pr10a/screenshots');
mkdirSync(shots, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(shots, `${name}.png`), fullPage: true });
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

async function ready(page: Page, path = '/') {
  await page.goto(`${BASE}${path}`);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
}
/** Client-side navigation keeps the in-page Mock backend and runtime (a reload would reset them). */
async function spa(page: Page, path: string) {
  await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
}
const nav = (page: Page, name: string) => page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name }).click();
async function createClass(page: Page, title: string) {
  await spa(page, '/classrooms');
  await page.getByLabel('课堂标题').fill(title);
  await page.getByRole('button', { name: '创建并进入准备' }).click();
  await expect(page).toHaveURL(/\/classrooms\/[0-9a-f-]{36}\/live$/);
  await expect(page.getByTestId('classroom-prep')).toBeVisible();
  return /classrooms\/([0-9a-f-]{36})\/live/.exec(page.url())![1]!;
}
async function startClass(page: Page) {
  const start = page.getByRole('button', { name: '开始采集' });
  await expect(start).toBeDisabled();
  await page.getByLabel(/我已了解以上用途/).check();
  await start.click();
  await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
}
const feed = (page: Page, times = 1) => (async () => { for (let i = 0; i < times; i++) await page.getByRole('button', { name: '推进转写' }).click(); })();
const entries = (page: Page) => page.getByTestId('transcript-timeline').locator('li[data-entry]');
const currentEpoch = (page: Page) => page.getByRole('region', { name: '模拟上传历史' }).locator('li', { hasText: '当前采集' }).getAttribute('data-capture-epoch');

test.describe('PR-10a classroom', () => {
  test('creating a classroom never opens capture; an explicit start persists across pages and theme (FE-01, UX-02)', async ({ page }) => {
    await ready(page);
    const id = await createClass(page, '第 6 讲 · 特征值');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    await expect(page.getByText(/点击「开始采集」前不会请求麦克风/)).toBeVisible();
    await page.getByRole('button', { name: '开始试音' }).click();
    await expect(page.getByRole('meter', { name: '模拟输入音量' })).not.toHaveAttribute('aria-valuenow', '0');
    await page.getByRole('button', { name: '结束试音' }).click();
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    await shot(page, 'prep-day');
    await startClass(page);
    const epoch = await currentEpoch(page);
    expect(epoch).toBeTruthy();
    await nav(page, '课程');
    await nav(page, '复习');
    await page.getByRole('link', { name: '外观设置' }).click();
    await page.getByLabel('Nebula Night').check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
    expect(await currentEpoch(page)).toBe(epoch);
    await nav(page, '课堂');
    await expect(page).toHaveURL(`${BASE}/classrooms/${id}/live`);
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
    await spa(page, '/classrooms');
    await expect(page).toHaveURL(`${BASE}/classrooms/${id}/live`);
  });

  test('partial vs final, late final inserted by time, duplicate ignored, reading position kept (UI-CLASS-07, ASR-04)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
    await createClass(page, '转写顺序');
    await startClass(page);
    await feed(page);
    await expect(entries(page)).toHaveCount(1);
    await expect(entries(page).first()).toHaveAttribute('data-status', 'partial');
    await expect(entries(page).first()).toContainText('临时');
    await feed(page);
    await expect(entries(page).first()).toHaveAttribute('data-status', 'final');
    await expect(entries(page).first()).not.toContainText('临时');
    await expect(page.getByTestId('classroom-flows').locator('[data-flow="asr"]')).toHaveAttribute('data-state', 'streaming');
    await feed(page, 4);
    await page.getByRole('button', { name: '送达迟到的早段' }).click();
    const starts = await entries(page).evaluateAll(items => items.map(item => Number(item.getAttribute('data-start'))));
    expect(starts).toEqual([...starts].sort((a, b) => a - b));
    await expect(entries(page).nth(2)).toContainText('补回的较早片段');
    const count = await entries(page).count();
    await page.getByRole('button', { name: '重复送达上一条' }).click();
    await expect(entries(page)).toHaveCount(count);
    // Reading back: new text must not move the reader.
    await feed(page, 30);
    const timeline = page.getByTestId('transcript-timeline');
    await expect(timeline).toHaveAttribute('data-following', 'true');
    await timeline.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
    await expect(timeline).toHaveAttribute('data-following', 'false');
    const firstText = await entries(page).first().textContent();
    await feed(page, 4);
    expect(await timeline.evaluate(element => element.scrollTop)).toBe(0);
    expect(await entries(page).first().textContent()).toBe(firstText);
    await expect(page.getByRole('button', { name: /有 \d+ 条新内容 · 回到最新/ })).toBeVisible();
    await page.getByRole('button', { name: /回到最新/ }).click();
    await expect(timeline).toHaveAttribute('data-following', 'true');
    await shot(page, 'live-three-columns-day');
  });

  test('three/two/one columns by container width; separators by keyboard; drafts survive layout, focus mode and navigation (UI-CLASS-02/03/12)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
    const first = await createClass(page, '布局与草稿');
    await startClass(page);
    await feed(page, 2);
    const workspace = page.locator('[data-layout]');
    await expect(workspace).toHaveAttribute('data-layout', 'three');
    const transcript = page.locator('section[data-panel="transcript"]');
    const before = await transcript.evaluate(element => element.getBoundingClientRect().width);
    const separator = page.getByRole('separator', { name: '调整原文与AI 解释的宽度' });
    await separator.focus();
    for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowLeft');
    const after = await transcript.evaluate(element => element.getBoundingClientRect().width);
    expect(before - after).toBeGreaterThan(60);
    // Minimum readable width always wins (PRD p18, CF-14).
    await page.keyboard.press('Home');
    expect(Math.round(await transcript.evaluate(element => element.getBoundingClientRect().width))).toBe(440);
    await page.keyboard.press('Enter');
    expect(Math.abs(await transcript.evaluate(element => element.getBoundingClientRect().width) - before)).toBeLessThan(3);
    await page.locator('section[data-panel="explanation"]').getByRole('button', { name: '折叠' }).click();
    await expect(page.locator('section[data-panel="explanation"]')).toBeHidden();
    await page.getByRole('button', { name: '展开AI 解释' }).click();
    await expect(page.locator('section[data-panel="explanation"]')).toBeVisible();

    const question = () => page.getByLabel(/向 AI 提问/);
    await question().fill('为什么特征向量不唯一？（草稿）');
    await page.setViewportSize({ width: 900, height: 900 });
    await expect(workspace).toHaveAttribute('data-layout', 'two');
    await page.getByRole('tab', { name: '问答' }).click();
    await expect(question()).toHaveValue('为什么特征向量不唯一？（草稿）');
    await shot(page, 'live-two-columns');
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(workspace).toHaveAttribute('data-layout', 'one');
    await page.getByRole('tab', { name: '问答' }).click();
    await expect(question()).toHaveValue('为什么特征向量不唯一？（草稿）');
    await shot(page, 'live-one-column-phone');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: '专注模式' }).click();
    await expect(page.locator('section[data-panel="qa"]')).toBeHidden();
    await expect(page.locator('section[data-panel="transcript"]')).toBeVisible();
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
    await page.getByRole('button', { name: '退出专注' }).click();
    await expect(workspace).toHaveAttribute('data-layout', 'three');
    await expect(question()).toHaveValue('为什么特征向量不唯一？（草稿）');
    await nav(page, '课程');
    await nav(page, '课堂');
    await expect(question()).toHaveValue('为什么特征向量不唯一？（草稿）');

    // Another classroom never sees this draft; the first keeps it.
    await page.getByRole('toolbar', { name: '课堂工具栏' }).getByRole('button', { name: '结束课堂' }).click();
    await expect(page).toHaveURL(/processing$/);
    await createClass(page, '第二节');
    await startClass(page);
    await feed(page, 2);
    await expect(question()).toHaveValue('');
    await spa(page, `/classrooms/${first}/live`);
    await expect(question()).toHaveValue('为什么特征向量不唯一？（草稿）');
    // A different account epoch discards drafts entirely.
    await page.getByRole('button', { name: '切换模拟账号 A' }).click();
    await page.getByRole('button', { name: '切换模拟账号 B' }).click();
    await spa(page, `/classrooms/${first}/live`);
    await expect(page.getByRole('heading', { name: '布局与草稿' })).toBeVisible();
    await expect(question()).toHaveValue('');
  });

  test('pause/resume, marks without and with a final, and slide versions (UI-CLASS-05/06/08)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
    await createClass(page, '工具栏');
    await startClass(page);
    const toolbar = page.getByRole('toolbar', { name: '课堂工具栏' });
    const pausedEpoch = await currentEpoch(page);
    await toolbar.getByRole('button', { name: '暂停' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('已暂停');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toContainText('已暂停');
    await toolbar.getByRole('button', { name: '继续' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
    // Resuming binds the new epoch issued by the server: the paused epoch's chunk becomes history.
    const history = page.getByRole('region', { name: '模拟上传历史' });
    await expect.poll(() => currentEpoch(page)).not.toBe(pausedEpoch);
    await expect(history.locator(`li[data-capture-epoch="${pausedEpoch}"]`)).toContainText('历史采集');
    await toolbar.getByRole('button', { name: '没懂' }).click();
    await expect(page.getByRole('status').filter({ hasText: '已标记没懂（当前时间点）' })).toBeVisible();
    await feed(page, 2);
    await toolbar.getByRole('button', { name: '重点' }).click();
    await expect(page.getByRole('status').filter({ hasText: /已标记重点（00:00）/ })).toBeVisible();
    await toolbar.getByRole('button', { name: '详情' }).click();
    const details = page.getByRole('dialog', { name: '课堂详情' });
    await expect(details.getByRole('list', { name: '本节课的标记' }).locator('li')).toHaveCount(2);
    await expect(details).toContainText('时间点标记');
    await expect(details).toContainText('关联到正式字幕');
    await page.keyboard.press('Escape');
    await toolbar.getByRole('button', { name: '课件' }).click();
    const slides = page.getByRole('dialog', { name: '课件图片' });
    await slides.getByLabel(/添加课件图片/).setInputFiles({ name: 'board.png', mimeType: 'image/png', buffer: PNG });
    await expect(slides.getByRole('status').filter({ hasText: '已添加「board」' })).toBeVisible();
    const item = slides.getByRole('list', { name: '课件图片' }).locator('li').first();
    const version = await item.getAttribute('data-slide-version');
    await item.getByRole('button', { name: '向右旋转' }).click();
    await expect(slides.getByText(/旧版本仍作为历史来源保留/)).toBeVisible();
    await expect(item).not.toHaveAttribute('data-slide-version', version!);
    await expect(item).toContainText('旋转 90°');
    await item.getByRole('button', { name: '移除' }).click();
    await expect(slides.getByText('已移除 1 张图片，历史来源仍保留原版本。')).toBeVisible();
  });

  test('ending a class: stop range first, local audio warned, partial summary needs explicit consent, result is a candidate (UI-CLASS-11, FE-09)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
    const id = await createClass(page, '课后处理');
    await startClass(page);
    await feed(page, 4);
    await page.getByRole('toolbar', { name: '课堂工具栏' }).getByRole('button', { name: '结束课堂' }).click();
    await expect(page).toHaveURL(`${BASE}/classrooms/${id}/processing`);
    await expect(page.getByTestId('processing-items').locator('[data-key="stop"]')).toContainText('已停止新的采集');
    await expect(page.getByTestId('leave-warning')).toContainText('本机仍有 1 个音频分片未上传');
    await expect(page.getByTestId('processing-items').locator('[data-key="local"]')).toHaveAttribute('data-tone', 'danger');
    await expect(page.getByTestId('processing-headline')).toContainText('尚未全部完成');
    await expect(page.getByTestId('summary-missing')).toContainText('转写仍在处理');
    await expect(page.getByRole('button', { name: '生成课堂总结…' })).toHaveCount(0);
    await shot(page, 'processing-partial');
    await page.getByRole('button', { name: '生成部分范围总结…' }).click();
    const dialog = page.getByTestId('plan-dialog');
    await expect(dialog).toContainText('材料不完整，将按部分范围生成');
    const send = dialog.getByRole('button', { name: '确认发送' });
    await expect(send).toBeDisabled();
    await dialog.getByLabel(/确认把上述内容发送给/).check();
    await expect(send).toBeDisabled();
    await dialog.getByLabel('我知道材料不完整，确认按部分范围生成').check();
    await send.click();
    await expect(page.getByRole('status').filter({ hasText: '总结请求已提交' })).toBeVisible();
    await page.getByRole('button', { name: '任务 → 完成' }).click();
    await expect(page.getByTestId('candidate-ready')).toContainText('候选不会自动替换已采用版本');
    await page.getByRole('button', { name: '转写收尾完成' }).click();
    await expect(page.getByTestId('summary-missing')).not.toContainText('转写仍在处理');
    await expect(page.getByRole('link', { name: '查看课堂记录' })).toBeVisible();
  });

  test('an unknown result can only be reconciled, never retried (UI-CLASS-10)', async ({ page }) => {
    await ready(page);
    await spa(page, `/classrooms/${UNKNOWN_AI_CLASS}/processing`);
    const unknown = page.getByTestId('task-list').locator('li[data-task-state="unknown"]');
    await expect(unknown).toContainText('结果未知，可能已计费');
    await expect(page.getByRole('button', { name: /重试/ })).toHaveCount(0);
    await unknown.getByRole('button', { name: '核对结果' }).click();
    await expect(unknown.getByRole('status')).toContainText('没有重新发送请求');
    await expect(unknown).toHaveAttribute('data-task-state', 'unknown');
  });

  test('only one classroom captures at a time; the PR-09a demo capture opens its classroom page', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '开始模拟课堂' }).click();
    await expect(page).toHaveURL(`${BASE}/classrooms/${DEMO_CLASS}/live`);
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
    await spa(page, '/classrooms');
    await expect(page).toHaveURL(`${BASE}/classrooms/${DEMO_CLASS}/live`);
    await spa(page, '/classrooms/c0000000-0000-4000-8000-000000000003/live');
    await expect(page.getByRole('heading', { name: '行列式的性质' })).toBeVisible();
    await expect(page.getByTestId('capture-phase')).toHaveText('已结束 · 处理中');
    await page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止', exact: true }).click();
    await spa(page, '/classrooms');
    await expect(page.getByRole('heading', { name: '课堂', exact: true })).toBeVisible();
  });

  test('another active capture blocks starting a second classroom', async ({ page }) => {
    await ready(page);
    const later = await createClass(page, '稍后再上的课');
    const active = await createClass(page, '正在采集的课');
    await startClass(page);
    await spa(page, `/classrooms/${later}/live`);
    await expect(page.getByTestId('other-capture')).toBeVisible();
    await page.getByLabel(/我已了解以上用途/).check();
    await expect(page.getByRole('button', { name: '开始采集' })).toBeDisabled();
    await page.getByTestId('other-capture').getByRole('link').click();
    await expect(page).toHaveURL(`${BASE}/classrooms/${active}/live`);
  });

  test('320 px with 200% text: no page overflow and 停止 stays reachable on the live page', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await ready(page);
    await createClass(page, '一个非常非常长的课堂标题用来检查窄屏换行与放大文字时不会撑出页面');
    await startClass(page);
    await feed(page, 4);
    await page.evaluate(() => {
      const elements = Array.from(document.querySelectorAll<HTMLElement>('body, body *'));
      const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
      elements.forEach((element, index) => { if (Number.isFinite(sizes[index])) element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important'); });
    });
    const overflow = await page.evaluate(() => ({ fits: document.documentElement.scrollWidth <= innerWidth, offenders: Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .filter(element => element.getBoundingClientRect().right > innerWidth + 1 && element.getClientRects().length > 0)
      .slice(0, 8).map(element => `${element.tagName}.${element.className} ${Math.round(element.getBoundingClientRect().right)} ${element.textContent?.slice(0, 30)}`) }));
    expect(overflow.fits, overflow.offenders.join(' | ')).toBe(true);
    await expect(page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止', exact: true })).toBeInViewport();
  });

  test('record page keeps time positions when original audio is unavailable', async ({ page }) => {
    await ready(page);
    await spa(page, '/classrooms/c0000000-0000-4000-8000-000000000004/record');
    await expect(page.getByTestId('audio-status')).toContainText('不能播放');
    await expect(entries(page)).toHaveCount(1);
    await expect(entries(page).first()).toContainText('00:00');
    await spa(page, '/classrooms/c0000000-0000-4000-8000-000000000001/record');
    await expect(entries(page)).toHaveCount(3);
    await expect(page.getByText('板书：特征多项式（合成）')).toBeVisible();
  });

  test('the global stop records the server stop range like 结束课堂 (review r0)', async ({ page }) => {
    await ready(page);
    const id = await createClass(page, '全局停止');
    await startClass(page);
    await page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止', exact: true }).click();
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    await spa(page, `/classrooms/${id}/processing`);
    await expect(page.getByTestId('processing-items').locator('[data-key="stop"]')).toContainText('已停止新的采集');
    await expect(page.getByRole('button', { name: '重试确认停止范围' })).toHaveCount(0);
  });

  test('a stop sent while offline stays retryable after reconnecting (review r0)', async ({ page }) => {
    await ready(page);
    const id = await createClass(page, '离线结束');
    await startClass(page);
    await page.getByLabel('模拟离线').check();
    await page.getByRole('toolbar', { name: '课堂工具栏' }).getByRole('button', { name: '结束课堂' }).click();
    await expect(page).toHaveURL(`${BASE}/classrooms/${id}/processing`);
    await expect(page.getByTestId('processing-items').locator('[data-key="stop"]')).toContainText('停止范围尚未被服务端确认');
    const retry = page.getByRole('button', { name: '重试确认停止范围' });
    await expect(retry).toBeDisabled();
    await page.getByLabel('模拟离线').uncheck();
    await retry.click();
    await expect(page.getByTestId('processing-items').locator('[data-key="stop"]')).toContainText('已停止新的采集');
    await expect(retry).toHaveCount(0);
  });

  test('a classroom deletion removes its content from the page and the server (review r0)', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '开始模拟课堂' }).click();
    await expect(page).toHaveURL(`${BASE}/classrooms/${DEMO_CLASS}/live`);
    await feed(page, 2);
    await expect(entries(page)).not.toHaveCount(0);
    await page.getByLabel('模拟事件脚本').selectOption('classroomDeletion');
    await page.getByRole('button', { name: '运行模拟事件' }).click();
    await expect(page.getByTestId('transcript-timeline')).toHaveCount(0);
    await expect(page.getByRole('toolbar', { name: '课堂工具栏' })).toHaveCount(0);
    await spa(page, `/classrooms/${DEMO_CLASS}/record`);
    await expect(page.getByRole('alert')).toContainText('返回课堂入口');
    await expect(page.getByTestId('transcript-timeline')).toHaveCount(0);
  });

  test('a pause sent offline is replayed on reconnect, so 继续 works afterwards (review r2)', async ({ page }) => {
    await ready(page);
    await createClass(page, '离线暂停');
    await startClass(page);
    const toolbar = page.getByRole('toolbar', { name: '课堂工具栏' });
    await page.getByLabel('模拟离线').check();
    await toolbar.getByRole('button', { name: '暂停' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('已暂停');
    await expect(page.getByRole('status').filter({ hasText: '已在本机暂停，服务端尚未确认' })).toBeVisible();
    await page.getByLabel('模拟离线').uncheck();
    await toolbar.getByRole('button', { name: '继续' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
  });

  test('a classroom whose course was deleted shows no cached content (review r2)', async ({ page }) => {
    await ready(page);
    await spa(page, '/classrooms');
    await page.getByRole('combobox', { name: '课程' }).selectOption({ label: '大学物理 II' });
    await page.getByLabel('课堂标题').fill('将被级联删除');
    await page.getByRole('button', { name: '创建并进入准备' }).click();
    await expect(page.getByTestId('classroom-prep')).toBeVisible();
    const id = /classrooms\/([0-9a-f-]{36})\/live/.exec(page.url())![1]!;
    await spa(page, '/courses/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3/overview');
    await page.getByRole('button', { name: /删除/ }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page).toHaveURL(/\/courses$/);
    await spa(page, `/classrooms/${id}/live`);
    await expect(page.getByRole('alert')).toContainText('返回课堂入口');
    await expect(page.getByTestId('classroom-prep')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '将被级联删除' })).toHaveCount(0);
  });

  test('an offline 继续 is completed on reconnect without a second click (review r3)', async ({ page }) => {
    await ready(page);
    await createClass(page, '离线继续');
    await startClass(page);
    const toolbar = page.getByRole('toolbar', { name: '课堂工具栏' });
    await toolbar.getByRole('button', { name: '暂停' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('已暂停');
    await page.getByLabel('模拟离线').check();
    await toolbar.getByRole('button', { name: '继续' }).click();
    await expect(page.getByRole('status').filter({ hasText: '未能继续' })).toBeVisible();
    await expect(page.getByTestId('capture-phase')).toHaveText('已暂停');
    await page.getByLabel('模拟离线').uncheck();
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
    await expect(toolbar.getByRole('button', { name: '暂停' })).toBeVisible();
  });

  test('deleting the course of the capturing classroom revokes the capture (review r3)', async ({ page }) => {
    await ready(page);
    await spa(page, '/classrooms');
    await page.getByRole('combobox', { name: '课程' }).selectOption({ label: '大学物理 II' });
    await page.getByLabel('课堂标题').fill('采集中的物理课');
    await page.getByRole('button', { name: '创建并进入准备' }).click();
    await expect(page.getByTestId('classroom-prep')).toBeVisible();
    await startClass(page);
    await spa(page, '/courses/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3/overview');
    await page.getByRole('button', { name: '删除…', exact: true }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page).toHaveURL(/\/courses$/);
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    // The revoked lease no longer blocks another classroom.
    await createClass(page, '删除后新课堂');
    await startClass(page);
  });

  test('a stopped classroom with local audio still guards unload after another classroom starts (review r3)', async ({ page }) => {
    await ready(page);
    await createClass(page, '先停止的课堂');
    await startClass(page);
    await page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止', exact: true }).click();
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    await createClass(page, '后开始的课堂');
    await startClass(page);
    // Closing the page asks first: the stopped classroom's audio is still only on this device.
    // (A synthetic beforeunload event would also stop the MSW worker, so the real close is used.)
    const dialog = new Promise<string>(resolve => page.once('dialog', item => { resolve(item.type()); void item.dismiss(); }));
    await page.close({ runBeforeUnload: true });
    expect(await dialog).toBe('beforeunload');
  });

  test('a session revocation during capture leaves no lease behind (review r3)', async ({ page }) => {
    await ready(page);
    await createClass(page, '撤销前的课堂');
    await startClass(page);
    await page.getByLabel('模拟事件脚本').selectOption('revocation');
    await page.getByRole('button', { name: '运行模拟事件' }).click();
    await page.getByRole('button', { name: '进入模拟账号 A' }).click();
    await expect(page.getByText('Mock API 已连接')).toBeVisible();
    await createClass(page, '撤销后的课堂');
    await startClass(page);
  });

  test('after a lost lease the classroom offers an explicit new start, not a pending stop (review r3)', async ({ page }) => {
    await ready(page);
    await createClass(page, '租约失效');
    await startClass(page);
    await page.getByLabel('模拟事件脚本').selectOption('leaseLoss');
    await page.getByRole('button', { name: '运行模拟事件' }).click();
    await expect(page.getByRole('alert').filter({ hasText: '采集租约已失效' })).toBeVisible();
    await expect(page.getByTestId('capture-phase')).toHaveText('准备中 · 未开始采集');
    await expect(page.getByText('停止范围尚未被服务端确认')).toHaveCount(0);
    await startClass(page);
  });
});
