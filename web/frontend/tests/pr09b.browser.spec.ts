import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:19309';
const LA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', DS = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', MP = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3';
const shots = resolve(process.cwd(), '../../outputs/pr09b/screenshots');
mkdirSync(shots, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(shots, `${name}.png`), fullPage: true });

async function ready(page: Page, path = '/') {
  await page.goto(`${BASE}${path}`);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
}
/** Client-side navigation: the synthetic backend lives in the page, so a reload would reset it. */
async function spa(page: Page, path: string) {
  await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
  await expect(page).toHaveURL(`${BASE}${path}`);
}
async function inOrder(locators: Locator[]) {
  const handles = await Promise.all(locators.map(locator => locator.elementHandle()));
  for (let index = 1; index < handles.length; index++) {
    expect(await handles[index - 1]!.evaluate((a, b) => Boolean(a.compareDocumentPosition(b!) & Node.DOCUMENT_POSITION_FOLLOWING), handles[index])).toBe(true);
  }
}
const modules = (page: Page) => ['继续学习', '快速开始', '最近课程', '待处理', '学习动态'].map(name => page.getByRole('heading', { name, exact: true }));
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }
async function doubleText(page: Page) {
  await page.evaluate(() => {
    const elements = Array.from(document.querySelectorAll<HTMLElement>('body, body *'));
    const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
    elements.forEach((element, index) => { if (Number.isFinite(sizes[index])) element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important'); });
  });
}

test.describe('PR-09b Dashboard', () => {
  test('module order is fixed with data and when empty; empty state has no invented numbers', async ({ page }) => {
    await ready(page);
    for (const heading of modules(page)) await expect(heading).toBeVisible();
    await inOrder(modules(page));
    await shot(page, 'dashboard-seeded-day');
    await page.getByLabel('模拟场景').selectOption('01');
    await expect(page.getByTestId('continue')).toHaveAttribute('data-kind', 'new');
    for (const heading of modules(page)) await expect(heading).toBeVisible();
    await inOrder(modules(page));
    await expect(page.getByRole('link', { name: /创建第一门课程/ })).toBeVisible();
    const text = await page.getByTestId('dashboard').innerText();
    expect(text).not.toMatch(/\d+\s*节课堂|积分|签到|连续学习|掌握度|评分|学习时长/);
    await expect(page.getByText('尚未创建学期')).toBeVisible();
    await expect(page.getByRole('button', { name: '开始模拟课堂' })).toHaveCount(0);
    await shot(page, 'dashboard-empty-day');
  });

  test('continue entry priority: active capture → local unsaved risk → most recent real class', async ({ page }) => {
    await ready(page);
    const entry = page.getByTestId('continue');
    await expect(entry).toHaveAttribute('data-kind', 'class_session');
    await expect(entry).toContainText('线性代数 · 特征值与特征向量');
    // Scenario 05 starts a simulated recording and a failed local write: capture still wins.
    await page.getByLabel('模拟场景').selectOption('05');
    await expect(entry).toHaveAttribute('data-kind', 'capture');
    await expect(page.getByRole('button', { name: '返回模拟课堂' })).toBeVisible();
    await page.getByRole('button', { name: '模拟写入失败' }).click();
    await expect(entry).toHaveAttribute('data-kind', 'capture');
    await page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止' }).click();
    await expect(entry).toHaveAttribute('data-kind', 'local_risk');
    await expect(entry).toContainText('尚未安全保存');
  });

  test('five flows: upload failure with normal capture is never shown complete; todos are real and locatable', async ({ page }) => {
    await ready(page);
    const recent = page.locator('[data-module="recent"]');
    const ds = recent.getByRole('link', { name: /数据结构与算法分析/ });
    await expect(ds.getByTestId('flow-summary')).toHaveAttribute('data-complete', 'false');
    await expect(ds).toContainText('上传：上传失败');
    await expect(ds).not.toContainText('五项处理均已完成');
    await expect(recent.getByRole('link', { name: /^线性代数/ }).getByTestId('flow-summary')).toHaveAttribute('data-complete', 'true');
    const todos = page.getByTestId('todo-list');
    for (const kind of ['conflict', 'failed', 'unknown', 'expiring', 'not_understood']) await expect(todos.locator(`li[data-kind="${kind}"]`).first()).toBeVisible();
    await expect(todos).toContainText('AI 结果未知，可能已计费');
    await expect(todos).toContainText('未懂：为什么特征向量不唯一？');
    // An unresolved question on an older class (not among the five shown) is still a todo.
    await expect(todos).toContainText('未懂：高斯面为什么可以任意选？');
    await todos.locator('li[data-kind="failed"]').getByRole('link').click();
    await expect(page).toHaveURL(/\/classrooms\/c0000000-0000-4000-8000-000000000004\/processing$/);
  });

  test('every loaded todo stays reachable beyond the preview', async ({ page }) => {
    await ready(page);
    await page.evaluate(() => {
      const mock = (globalThis as unknown as { __classroomMock: { bump: (k: string, i: string, c: object) => void } }).__classroomMock;
      for (const n of [2, 3, 5, 7]) mock.bump('class_session', `c0000000-0000-4000-8000-00000000000${n}`, { flows: { capture: 'ended', upload: 'failed', asr: 'succeeded', ai: 'idle', sync: 'synced' } });
    });
    // Re-entering account A clears the query cache, so the dashboard reads the changed records.
    await page.getByRole('button', { name: '切换模拟账号 A' }).click();
    await page.getByRole('button', { name: '切换模拟账号 B' }).click();
    const todos = page.getByTestId('todo-list');
    await expect(todos.locator('li[data-kind="failed"]').first()).toBeVisible();
    // Unresolved questions are read per classroom after the core lists: count only once both seeded ones
    // (classrooms 1 and 7) are in.
    await page.getByRole('button', { name: /^显示全部 \d+ 项$/ }).click();
    await expect(todos.locator('li[data-kind="not_understood"]')).toHaveCount(2);
    await page.getByRole('button', { name: '收起' }).click();
    const toggle = page.getByRole('button', { name: /^显示全部 \d+ 项$/ });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    const total = Number((await toggle.innerText()).match(/\d+/)![0]);
    expect(total).toBeGreaterThan(6);
    await expect(todos.locator('li')).toHaveCount(6);
    await toggle.click();
    await expect(todos.locator('li')).toHaveCount(total);
    await expect(page.getByRole('button', { name: '收起' })).toHaveAttribute('aria-expanded', 'true');
  });

  test('deleting a course removes its entries and todos everywhere (UI-DASH-09)', async ({ page }) => {
    await ready(page, `/courses/${MP}/overview`);
    await page.getByRole('button', { name: /删除/ }).click();
    const dialog = page.getByTestId('delete-dialog');
    await expect(dialog.getByTestId('delete-impact')).toContainText('2 节');
    await page.getByRole('button', { name: '取消' }).click();
    await expect(page.getByRole('heading', { name: '大学物理 II', level: 1 })).toBeVisible();
    await page.getByRole('button', { name: /删除/ }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page).toHaveURL(/\/courses$/);
    await expect(page.getByText('大学物理 II')).toHaveCount(0);
    await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
    await expect(page.getByTestId('dashboard')).not.toContainText('大学物理');
    await expect(page.getByTestId('todo-list').locator('li[data-kind="conflict"]')).toHaveCount(0);
    await spa(page, `/courses/${MP}/overview`);
    await expect(page.getByRole('heading', { name: '课程不存在或已删除' })).toBeVisible();
  });

  test('offline states the available scope, keeps already-read metadata read-only and disables writes', async ({ page }) => {
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    await ready(page, '/courses');
    await page.getByTestId('course-cards').getByRole('link', { name: /^线性代数/ }).click();
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toBeVisible();
    await nav.getByRole('link', { name: '首页' }).click();
    await page.getByLabel('模拟离线').check();
    await expect(page.getByTestId('offline-banner')).toContainText('当前离线');
    await expect(page.getByRole('link', { name: '＋ 新建课程' })).toHaveCount(0);
    await nav.getByRole('link', { name: '课程' }).click();
    await expect(page.getByTestId('offline-banner')).toBeVisible();
    await expect(page.getByRole('link', { name: /新建课程/ })).toHaveCount(0);
    await page.getByTestId('course-cards').getByRole('link', { name: /^线性代数/ }).click();
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: /归档/ })).toHaveCount(0);
    await shot(page, 'course-detail-offline');
    // UI and transport share one connectivity state across an account switch.
    await page.getByRole('button', { name: '切换模拟账号 A' }).click();
    await expect(page.getByLabel('模拟离线')).toBeChecked();
    await expect(page.getByTestId('offline-banner')).toBeVisible();
  });
});

test.describe('PR-09b Courses', () => {
  test('semester-first list: filters, sort and view stay in the URL and survive reload', async ({ page }) => {
    await ready(page, '/courses');
    await expect(page.getByLabel('学期')).toHaveValue('11111111-1111-4111-8111-111111111111');
    await expect(page.getByTestId('course-cards').getByRole('heading')).toHaveText(['线性代数', '数据结构与算法分析（双语）', '大学物理 II']);
    await page.getByLabel('搜索课程').fill('physics');
    await expect(page).toHaveURL(/q=physics/);
    await expect(page.getByTestId('course-cards').getByRole('heading')).toHaveText(['大学物理 II']);
    await page.getByRole('button', { name: /列表/ }).click();
    await page.getByLabel('排序').selectOption('name');
    await expect(page).toHaveURL(/view=list/);
    await expect(page).toHaveURL(/q=physics/);
    await expect(page).toHaveURL(/sort=name/);
    await page.reload();
    await expect(page.getByLabel('搜索课程')).toHaveValue('physics');
    await expect(page.getByRole('button', { name: /列表/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('course-table')).toContainText('大学物理 II');
    await page.getByRole('link', { name: '大学物理 II' }).click();
    await page.getByRole('link', { name: '← 课程列表' }).click();
    await expect(page).toHaveURL(/view=list/);
    await expect(page).toHaveURL(/q=physics/);
  });

  test('two list changes in the same tick both reach the URL', async ({ page }) => {
    await ready(page, '/courses');
    await expect(page.getByTestId('course-cards')).toBeVisible();
    // Fire both changes before React re-renders: the second must not overwrite the first.
    await page.evaluate(() => {
      const listButton = Array.from(document.querySelectorAll('button')).find(button => button.textContent?.includes('列表'))!;
      const sort = document.getElementById(Array.from(document.querySelectorAll('label')).find(label => label.textContent?.trim() === '排序')!.htmlFor) as HTMLSelectElement;
      listButton.click();
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
      setter.call(sort, 'name');
      sort.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await expect(page).toHaveURL(/view=list/);
    await expect(page).toHaveURL(/sort=name/);
    await expect(page.getByTestId('course-table')).toBeVisible();
  });

  test('same-name courses stay separate with their own IDs; creating another same-name course does not merge', async ({ page }) => {
    await ready(page, '/courses?semester=all');
    const cards = page.getByTestId('course-cards').locator('a', { has: page.getByRole('heading', { name: '线性代数', exact: true }) });
    await expect(cards).toHaveCount(2);
    const ids = await cards.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-course-id')));
    expect(new Set(ids).size).toBe(2);
    await expect(cards.first()).toContainText('同名课程 · 各自独立');
    await page.goto(`${BASE}/courses/new`);
    await page.getByLabel('课程名称（必填）').fill('线性代数');
    await expect(page.getByText(/已有 2 门同名课程/)).toBeVisible();
    await page.getByRole('button', { name: '创建课程' }).click();
    await expect(page).toHaveURL(/\/courses\/[0-9a-f-]{36}\/overview$/);
    const newId = page.url().split('/')[4];
    expect([LA, ...ids]).not.toContain(newId);
    await spa(page, '/courses?semester=all');
    await expect(cards).toHaveCount(3);
  });

  test('required fields, defaults and keyboard focus on validation errors', async ({ page }) => {
    await ready(page, '/courses/new');
    await expect(page.getByLabel('课堂语言')).toHaveValue('auto');
    await expect(page.getByLabel('所属学期（必填）')).toHaveValue('11111111-1111-4111-8111-111111111111');
    await page.getByLabel('所属学期（必填）').selectOption('');
    await page.getByRole('button', { name: '创建课程' }).click();
    const name = page.getByLabel('课程名称（必填）');
    await expect(name).toBeFocused();
    await expect(name).toHaveAttribute('aria-invalid', 'true');
    await expect(name).toHaveAccessibleDescription('请输入课程名称。');
    await expect(page.getByLabel('所属学期（必填）')).toHaveAccessibleDescription('请选择所属学期。');
    await page.goto(`${BASE}/semesters/new`);
    await page.getByLabel('学期名称（必填）').fill('2019 秋季（历史）');
    await page.getByLabel('开始日期').fill('2019-09-10');
    await page.getByLabel('结束日期').fill('2019-09-01');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.getByLabel('结束日期')).toBeFocused();
    await expect(page.getByLabel('结束日期')).toHaveAccessibleDescription('结束日期不能早于开始日期。');
    await page.getByLabel('结束日期').fill('2020-01-15');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page).toHaveURL(/\/courses\?semester=/);
    // The address changes before the router's transition commits: wait for the semester form to leave.
    await expect(page.getByRole('form', { name: '学期信息' })).toHaveCount(0);
    await expect(page.getByLabel('学期')).toContainText('2019 秋季（历史）');
  });

  test('CAS conflict keeps the user input and lets the user choose; the ID never changes', async ({ page }) => {
    await ready(page, `/courses/${LA}/edit`);
    await page.getByLabel('课程名称（必填）').fill('线性代数（我的修改）');
    await page.evaluate(id => (globalThis as unknown as { __classroomMock: { bump: (k: string, i: string, c: object) => void } }).__classroomMock.bump('course', id, { name: '线性代数（其他设备）' }), LA);
    await page.getByRole('button', { name: '保存' }).click();
    const conflict = page.getByTestId('conflict-panel');
    await expect(conflict).toContainText('名称：线性代数（我的修改）');
    await expect(conflict).toContainText('名称：线性代数（其他设备）');
    await expect(page.getByLabel('课程名称（必填）')).toHaveValue('线性代数（我的修改）');
    await shot(page, 'course-edit-conflict');
    await page.getByRole('button', { name: '以我的输入覆盖最新版本' }).click();
    await expect(page).toHaveURL(`${BASE}/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '线性代数（我的修改）', level: 1 })).toBeVisible();
  });

  test('semester CAS conflict can also discard local edits and load the latest', async ({ page }) => {
    await ready(page, '/semesters/11111111-1111-4111-8111-111111111111/edit');
    await page.getByLabel('学年').fill('我的学年');
    await page.evaluate(() => (globalThis as unknown as { __classroomMock: { bump: (k: string, i: string, c: object) => void } }).__classroomMock.bump('semester', '11111111-1111-4111-8111-111111111111', { academic_year: '其他设备学年' }));
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.getByTestId('conflict-panel')).toBeVisible();
    await page.getByRole('button', { name: '放弃我的修改，载入最新版本' }).click();
    await expect(page.getByLabel('学年')).toHaveValue('其他设备学年');
    await expect(page.getByTestId('conflict-panel')).toHaveCount(0);
  });

  test('four tabs: real routes, focus moves to the panel, empty states for a new course', async ({ page }) => {
    await ready(page, `/courses/${LA}/overview`);
    const tabs = page.getByRole('navigation', { name: '课程内容' });
    await expect(tabs.getByRole('link')).toHaveText(['概览', '课堂', '资料', '复习']);
    await expect(tabs.getByRole('link', { name: '概览' })).toHaveAttribute('aria-current', 'page');
    await tabs.getByRole('link', { name: '课堂' }).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(`${BASE}/courses/${LA}/classrooms`);
    await expect(page.locator('#course-tab-heading')).toBeFocused();
    await expect(page.getByTestId('classroom-table')).toContainText('特征值与特征向量');
    await tabs.getByRole('link', { name: '复习' }).click();
    await expect(page.getByTestId('course-review-list')).toContainText('特征值与特征向量 · 课堂总结');
    await expect(page.getByTestId('course-review-list')).not.toContainText('红黑树');
    await page.getByLabel('类型').selectOption('card');
    await expect(page).toHaveURL(/kind=card/);
    await expect(page.getByTestId('course-review-list').locator('li')).toHaveCount(1);
    await page.goto(`${BASE}/courses/new?semester=11111111-1111-4111-8111-111111111111`);
    await page.getByLabel('课程名称（必填）').fill('空课程');
    await page.getByRole('button', { name: '创建课程' }).click();
    await expect(page.getByText('还没有课堂').first()).toBeVisible();
    for (const [tab, text] of [['课堂', '还没有课堂'], ['资料', '课程大纲与术语词表'], ['复习', '这门课还没有复习资料']] as const) {
      await tabs.getByRole('link', { name: tab }).click();
      await expect(page.getByText(text).first()).toBeVisible();
    }
    const bogus = new URL(page.url()).pathname.replace(/review$/, 'bogus');
    await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, bogus);
    await expect(page).toHaveURL(`${BASE}${bogus.replace(/bogus$/, 'overview')}`);
  });

  test('moving a class is explicit and keeps counts consistent', async ({ page }) => {
    await ready(page, `/courses/${LA}/classrooms`);
    const row = page.getByTestId('classroom-table').locator('tr', { hasText: '行列式的性质' });
    const before = await row.locator('td').nth(1).innerText();
    await row.getByRole('button', { name: '改归属…' }).click();
    await expect(page.getByRole('dialog')).toContainText('采集时间、转写和历史来源保持不变');
    await page.getByLabel('移到课程').selectOption(DS);
    await page.getByRole('button', { name: '确认移动' }).click();
    await expect(page.getByTestId('classroom-table').locator('tr', { hasText: '行列式的性质' })).toHaveCount(0);
    await expect(page.getByText('2 节课堂')).toBeVisible();
    await spa(page, `/courses/${DS}/classrooms`);
    const moved = page.getByTestId('classroom-table').locator('tr', { hasText: '行列式的性质' });
    await expect(moved.locator('td').nth(1)).toHaveText(before);
    await expect(page.getByText('3 节课堂')).toBeVisible();
  });

  test('archive keeps content and is reversible; materials save with versioning', async ({ page }) => {
    await ready(page, `/courses/${DS}/overview`);
    await page.getByRole('button', { name: '归档' }).click();
    await expect(page.getByText('已归档 · 内容全部保留')).toBeVisible();
    await spa(page, '/courses');
    await expect(page.getByTestId('course-cards')).not.toContainText('数据结构');
    await page.getByLabel('只看已归档').check();
    await expect(page.getByTestId('course-cards')).toContainText('数据结构与算法分析（双语）');
    await expect(page.getByTestId('course-cards')).toContainText('2 节课堂');
    await spa(page, `/courses/${LA}/materials`);
    await expect(page.getByRole('textbox', { name: '课程大纲', exact: true })).toHaveValue(/合成大纲/);
    await page.getByLabel('资料语言', { exact: true }).selectOption('en');
    await expect(page.getByText('只影响之后课堂的默认设置')).toBeVisible();
    await page.getByRole('button', { name: '添加术语' }).click();
    await page.getByLabel('术语 2', { exact: true }).fill('eigenvector');
    await page.getByLabel('释义 2', { exact: true }).fill('特征向量');
    await page.getByRole('button', { name: '保存资料' }).click();
    const savedNotice = page.getByRole('status').filter({ hasText: '已保存到云端（版本 3）' });
    await expect(savedNotice).toBeVisible();
    await page.getByRole('textbox', { name: '课程大纲', exact: true }).fill('尚未保存的修改');
    await expect(savedNotice).toHaveCount(0);
    // The saved version has a new ID; the editor stays mounted and the next save uses the new revision.
    await page.getByRole('button', { name: '保存资料' }).click();
    await expect(page.getByRole('status').filter({ hasText: '已保存到云端（版本 4）' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: '课程大纲', exact: true })).toHaveValue('尚未保存的修改');
    await expect(page.getByText('课件照片和板书图片在各自课堂中上传与管理')).toBeVisible();
  });

  test('changing review filters keeps the course-list return context', async ({ page }) => {
    await ready(page, '/courses?view=list&sort=name');
    await page.getByTestId('course-table').getByRole('link', { name: '线性代数', exact: true }).click();
    await page.getByRole('navigation', { name: '课程内容' }).getByRole('link', { name: '复习' }).click();
    await page.getByLabel('类型').selectOption('card');
    await expect(page).toHaveURL(/kind=card/);
    await page.getByRole('link', { name: '← 课程列表' }).click();
    await expect(page).toHaveURL(/view=list/);
    await expect(page).toHaveURL(/sort=name/);
  });

  test('editing a course keeps the course-list return context on save and cancel', async ({ page }) => {
    await ready(page, '/courses?view=list&sort=name');
    await page.getByTestId('course-table').getByRole('link', { name: '线性代数', exact: true }).click();
    await page.getByRole('link', { name: /编辑/ }).click();
    await page.getByRole('link', { name: '取消' }).click();
    await page.getByRole('link', { name: /编辑/ }).click();
    await page.getByLabel('教师').fill('合成教师');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toBeVisible();
    await page.getByRole('link', { name: '← 课程列表' }).click();
    await expect(page).toHaveURL(/view=list/);
    await expect(page).toHaveURL(/sort=name/);
  });

  test('A/B isolation: B sees only its own courses and cannot open A’s course URL', async ({ page }) => {
    await ready(page, '/courses');
    await expect(page.getByTestId('course-cards')).toContainText('线性代数');
    await page.getByRole('button', { name: '切换模拟账号 A' }).click();
    await spa(page, '/courses');
    await expect(page.getByTestId('course-cards').getByRole('heading')).toHaveText(['组合数学（模拟账号 B）']);
    await spa(page, `/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '课程不存在或已删除' })).toBeVisible();
    await expect(page.getByText('线性代数')).toHaveCount(0);
  });
});

test.describe('PR-09b Settings', () => {
  test('AI preferences expose no key, model URL or BYOK field; quota and sessions are shown', async ({ page }) => {
    await ready(page, '/settings/ai');
    const form = page.getByTestId('ai-preferences');
    await expect(form.getByRole('textbox')).toHaveCount(0);
    await expect(page.getByLabel(/key|密钥|url|地址|模型/i)).toHaveCount(0);
    await expect(form).toContainText('不支持自带 API Key');
    await expect(form.getByRole('checkbox', { name: /自动解释/ })).not.toBeChecked();
    await page.getByRole('navigation', { name: '设置分区' }).getByRole('link', { name: '个人配额' }).click();
    await expect(page.getByTestId('quota')).toContainText('本月问答与解释次数');
    await expect(page.getByTestId('quota')).toContainText('结果未知，待核对');
    await page.getByRole('link', { name: '设备会话' }).click();
    await expect(page.getByText('当前会话', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(page.getByText('已撤销').first()).toBeVisible();
    await page.getByRole('link', { name: '数据与缓存' }).click();
    await expect(page.getByTestId('not-yet')).toContainText('尚未开放');
    await page.goto(`${BASE}/settings/unknown`);
    await expect(page).toHaveURL(/\/settings\/appearance$/);
  });

  test('a preference conflict keeps the draft and needs an explicit choice', async ({ page }) => {
    await ready(page, '/settings/ai');
    const language = page.getByLabel('解释语言');
    await language.selectOption('en');
    await page.evaluate(() => (globalThis as unknown as { __classroomMock: { bump: (k: string, i: string, c: object) => void } }).__classroomMock.bump('preferences', '', { automatic_explanation: false }));
    await page.getByTestId('ai-preferences').getByRole('button', { name: '保存' }).click();
    await expect(page.getByTestId('conflict-panel')).toBeVisible();
    await expect(language).toHaveValue('en');
    await page.getByRole('button', { name: '以我的修改覆盖最新版本' }).click();
    await expect(page.getByTestId('conflict-panel')).toHaveCount(0);
    await expect(language).toHaveValue('en');
    await expect(page.getByTestId('ai-preferences').getByRole('button', { name: '保存' })).toBeDisabled();
  });

  test('discarding a preference conflict loads the latest revision; the next save does not conflict again', async ({ page }) => {
    await ready(page, '/settings/ai');
    const form = page.getByTestId('ai-preferences');
    const language = page.getByLabel('解释语言');
    await language.selectOption('en');
    // Another device changes an unrelated preference.
    await page.evaluate(() => (globalThis as unknown as { __classroomMock: { bump: (k: string, i: string, c: object) => void } }).__classroomMock.bump('preferences', '', { reading_font_px: 20 }));
    await form.getByRole('button', { name: '保存' }).click();
    await expect(page.getByTestId('conflict-panel')).toBeVisible();
    await page.getByRole('button', { name: '放弃我的修改，载入最新版本' }).click();
    await expect(page.getByTestId('conflict-panel')).toHaveCount(0);
    await expect(language).toHaveValue('auto');
    await language.selectOption('zh');
    await form.getByRole('button', { name: '保存' }).click();
    await expect(form.getByRole('button', { name: '保存' })).toBeDisabled();
    await expect(page.getByTestId('conflict-panel')).toHaveCount(0);
    await expect(language).toHaveValue('zh');
  });

  test('profile and reading preference save with revisions; theme switch keeps an active capture', async ({ page }) => {
    await ready(page);
    await page.getByRole('button', { name: '开始模拟课堂' }).click();
    await spa(page, '/settings/profile');
    await page.getByLabel('昵称').fill('林同学（改）');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.getByText('已保存。')).toBeVisible();
    await page.getByLabel('昵称').fill('林同学（再改）');
    await expect(page.getByText('已保存。')).toHaveCount(0);
    await page.getByRole('link', { name: '主题与阅读' }).click();
    await page.getByLabel('Nebula Night').check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
    await page.getByLabel(/正文与字幕字号/).fill('20');
    await page.getByRole('button', { name: '保存字号' }).click();
    await expect(page.getByRole('button', { name: '保存字号' })).toBeDisabled();
    await shot(page, 'settings-appearance-night');
  });
});

test.describe('PR-09b layout: 320px, 200% text, both themes', () => {
  for (const theme of ['day', 'night'] as const) {
    test(`pages reflow without horizontal overflow in ${theme}`, async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem('aurora-theme', value), theme);
      await page.setViewportSize({ width: 320, height: 800 });
      for (const path of ['/dashboard', '/courses', `/courses/${LA}/overview`, `/courses/${LA}/classrooms`, `/courses/${LA}/materials`, '/courses/new', '/settings/usage']) {
        await ready(page, path);
        await expect(page.locator('main')).toBeVisible();
        await noOverflow(page);
        await doubleText(page);
        await noOverflow(page);
        await shot(page, `${theme}-320-200pct-${path.replaceAll('/', '_').replace(/^_/, '') || 'root'}`);
      }
    });
  }
  test('a very long course name wraps on cards and detail', async ({ page }) => {
    await ready(page, '/courses/new');
    const long = '线性代数与机器学习中的矩阵分解及其跨学科应用课程名称超长示例LinearAlgebraAndMatrixFactorizationForInterdisciplinaryApplications';
    await page.getByLabel('课程名称（必填）').fill(long);
    await page.getByRole('button', { name: '创建课程' }).click();
    await page.setViewportSize({ width: 320, height: 800 });
    await expect(page.getByRole('heading', { name: long, level: 1 })).toBeVisible();
    await noOverflow(page);
    await spa(page, '/courses');
    await expect(page.getByRole('heading', { name: long })).toBeVisible();
    await noOverflow(page);
  });
});
