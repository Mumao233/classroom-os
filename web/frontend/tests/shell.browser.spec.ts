import { expect, test } from '@playwright/test';

test('production preview reports unavailability without mock data or worker', async ({ page }) => {
  await page.goto('http://127.0.0.1:19310/');
  await expect(page.getByRole('heading', { name: '服务暂不可用' })).toBeVisible();
  await expect(page.getByText('开发预览 · 合成数据')).toHaveCount(0);
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0);
});

test('unmatched business request fails closed and known mock response is returned', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  const result = await page.evaluate(async () => {
    const known = await fetch('/api/web/v1/courses').then(response => response.json());
    const missing = await fetch('/api/web/v1/not-implemented').then(response => response.status, () => 0);
    return { known, missing };
  });
  expect(result.known).toEqual({ items: [], next_cursor: null });
  expect(result.missing).toBe(0);
});

test('capture remains visible during navigation and theme change', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await expect(bar).toBeVisible();
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '复习' }).click();
  await expect(bar).toBeVisible();
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '设置' }).click();
  await page.getByLabel('Nebula Night').check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
  await expect(bar).toBeVisible();
  await bar.getByRole('button', { name: '停止' }).click();
  await expect(bar).toHaveCount(0);
});

test('320px and 200% text have no document horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('http://127.0.0.1:19309/');
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
  await expect(page.getByRole('navigation', { name: '移动主导航' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const stop = page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止' });
  await expect(stop).toBeInViewport();
  await stop.click();
  await expect(stop).toHaveCount(0);
});

test('mock fault scenarios keep upload, local write and unknown cost states distinct', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByLabel('模拟场景').selectOption('04');
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await expect(bar).toContainText('采集 · 录音中');
  await expect(bar).toContainText('上传 · 失败');
  await expect(bar).toContainText('转写 · 进行中');
  await page.getByLabel('模拟场景').selectOption('05');
  await expect(bar).toContainText('未安全保存');
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  await expect(bar).toContainText('仅模拟本地保存');
  await page.getByRole('button', { name: '模拟写入失败' }).click();
  await expect(bar).toContainText('未安全保存');
  await page.getByLabel('模拟场景').selectOption('06');
  await expect(page.getByText('请求可能已计费。请先核对，不会自动重试或发起真实请求。')).toBeVisible();
  await page.getByRole('button', { name: '核对状态' }).click();
  await expect(page.getByText('模拟核对：结果仍未知；未重新发送。')).toBeVisible();
  await expect(page.getByRole('button', { name: /重试/ })).toHaveCount(0);
});

test('catalog input validation and dialog keyboard focus work', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/__dev/catalog');
  const input = page.getByRole('textbox', { name: '课程名称' });
  await page.getByRole('button', { name: '检查输入' }).click();
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  const id = await input.getAttribute('aria-describedby');
  expect(id).toBeTruthy();
  await expect(page.locator(`[id="${id}"]`)).toHaveText('请输入课程名称。');
  const trigger = page.getByRole('button', { name: '打开示例对话框' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '确认示例操作' });
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('Tab');
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('reduced motion and low effects remove blur without hiding navigation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('http://127.0.0.1:19309/');
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  await expect(nav).toBeVisible();
  await expect(nav).toHaveCSS('backdrop-filter', 'none');
  await nav.getByRole('link', { name: '设置' }).click();
  await page.getByLabel('低特效模式').check();
  await expect(page.locator('html')).toHaveAttribute('data-effects', 'reduced');
  await expect(nav).toHaveCSS('backdrop-filter', 'none');
});

test('recording can be stopped while dialog is open and focus returns', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await page.getByRole('link', { name: /组件目录/ }).click();
  const trigger = page.getByRole('button', { name: '打开示例对话框' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '确认示例操作' });
  await dialog.getByRole('button', { name: '停止模拟课堂' }).click();
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

test('active class navigation retains upload failure; account switch clears displayed A data', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByLabel('模拟场景').selectOption('04');
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '复习' }).click();
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '课堂' }).click();
  await expect(page).toHaveURL(/classrooms\/mock\/live/);
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toContainText('上传 · 失败');
  await page.getByRole('button', { name: '切换模拟账号 A' }).click();
  await expect(page.getByRole('heading', { name: /陈同学/ })).toBeVisible();
  await expect(page.getByText('线性代数')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
});

test('late A request does not restore A page after switching to B', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mock-test-delay', '1'));
  await page.goto('http://127.0.0.1:19309/');
  await page.getByLabel('模拟场景').selectOption('11');
  await expect(page.getByText('Mock API 连接中')).toBeVisible();
  await page.getByRole('button', { name: '切换模拟账号 A' }).click();
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.waitForTimeout(1500); // The browser-test fixture has a fixed 1200 ms delay.
  await expect(page.getByRole('heading', { name: /陈同学/ })).toBeVisible();
  await expect(page.getByText('线性代数')).toHaveCount(0);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
});

test('MOCK-01/02 provide empty and synthetic dashboard states in order', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByLabel('模拟场景').selectOption('01');
  await expect(page.getByRole('link', { name: /创建第一门课程/ })).toBeVisible();
  await expect(page.getByText('线性代数')).toHaveCount(0);
  await page.getByLabel('模拟场景').selectOption('02');
  const labels = await page.locator('main h2').allTextContents();
  expect(labels.indexOf('特征值与特征向量')).toBeLessThan(labels.indexOf('准备好开始了吗？'));
  expect(labels.indexOf('最近课程')).toBeLessThan(labels.findIndex(value => value.includes('待处理')));
  expect(labels.findIndex(value => value.includes('待处理'))).toBeLessThan(labels.findIndex(value => value.includes('学习动态')));
});

test('MOCK-03/07/08/09/10/12 expose their synthetic controls', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  const selector = page.getByLabel('模拟场景');
  await selector.selectOption('03');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
  await selector.selectOption('07');
  await page.getByRole('button', { name: '查看对比' }).click();
  await expect(page.getByText('个人稿：合成的原有笔记。')).toBeVisible();
  await expect(page.getByText('候选：合成的另一版本。')).toBeVisible();
  await selector.selectOption('08');
  await page.getByRole('button', { name: '查看对比' }).click();
  await expect(page.getByText('云端：合成的另一版本。')).toBeVisible();
  for (const action of ['合并', '另存', '采用云端']) {
    await page.getByRole('button', { name: `模拟${action}` }).click();
    await expect(page.getByRole('status').filter({ hasText: `已选择模拟${action}路径` })).toContainText('个人稿与云端版本仍保留');
    await expect(page.getByText('个人稿：合成的原有笔记。')).toBeVisible();
    await expect(page.getByText('云端：合成的另一版本。')).toBeVisible();
  }
  await selector.selectOption('09');
  await expect(page.getByRole('button', { name: '播放原音不可用' })).toBeDisabled();
  await expect(page.getByText(/保留位置 00:12:30/)).toBeVisible();
  await selector.selectOption('10');
  await page.getByRole('link', { name: '前往复习入口' }).click();
  await expect(page).toHaveURL(/\/review/);
  await selector.selectOption('12');
  await page.getByRole('link', { name: /组件目录/ }).click();
  await expect(page.getByRole('heading', { name: '基础组件与状态' })).toBeVisible();
});

test('long formula scrolls locally and long course name remains available at 320px and 200% text', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('http://127.0.0.1:19309/__dev/catalog');
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
  await expect(page.getByTestId('long-course-name')).toBeVisible();
  const formula = page.getByRole('region', { name: '长公式示例' });
  await expect(formula).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await formula.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  await formula.focus();
  await expect(formula).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => formula.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
});
