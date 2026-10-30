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
  expect(result.known.next_cursor).toBeNull();
  expect(result.known.items.map((item: { name: string }) => item.name)).toEqual(['线性代数', '数据结构与算法分析（双语）', '大学物理 II']);
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

test('320px and 200% root font size have no document horizontal overflow', async ({ page }) => {
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
  await page.getByRole('button', { name: '模拟联网核对' }).click();
  await expect(page.getByText('模拟联网核对完成；没有真实上传。')).toBeVisible();
  await expect(bar).toContainText('同步 · 已同步');
  await page.getByRole('button', { name: '模拟写入失败' }).click();
  await expect(bar).toContainText('未安全保存');
  await page.getByRole('button', { name: '停止模拟课堂' }).click();
  await expect(bar).toHaveCount(0);
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
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
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
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
  await expect(page.getByText('线性代数')).toHaveCount(0);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
});

test('MOCK-01/02 provide empty and synthetic dashboard states in order', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByLabel('模拟场景').selectOption('01');
  await expect(page.getByRole('link', { name: /创建第一门课程/ })).toBeVisible();
  await expect(page.getByText('线性代数')).toHaveCount(0);
  await page.getByLabel('模拟场景').selectOption('02');
  await expect(page.getByRole('heading', { name: '最近课程' })).toHaveCount(1);
  const sections = [page.getByRole('heading', { name: /特征值与特征向量/ }), page.getByText('快速开始', { exact: true }), page.getByRole('heading', { name: '线性代数', exact: true }), page.getByRole('heading', { name: '待处理' }), page.getByRole('heading', { name: '学习动态' })];
  for (const section of sections) await expect(section).toBeVisible();
  const nodes = await Promise.all(sections.map(section => section.elementHandle()));
  for (let index = 1; index < nodes.length; index++) {
    expect(await nodes[index - 1]!.evaluate((earlier, later) => Boolean(earlier.compareDocumentPosition(later!) & Node.DOCUMENT_POSITION_FOLLOWING), nodes[index])).toBe(true);
  }
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
  await expect(page.getByRole('heading', { name: '模拟只读资料' })).toBeVisible();
  await expect(page.getByText('合成的学习资料示例：额度限制时，已有资料仍可阅读。')).toBeVisible();
  await selector.selectOption('12');
  await page.getByRole('link', { name: /组件目录/ }).click();
  await expect(page.getByRole('heading', { name: '基础组件与状态' })).toBeVisible();
});

test('long formula scrolls locally and long course name remains available at 320px and 200% root font size', async ({ page }) => {
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

test('approved desktop capsule remains viewport-centred and independent of the side regions', async ({ page }) => {
  for (const width of [768, 800, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('http://127.0.0.1:19309/');
    const nav = page.getByTestId('desktop-nav');
    const brand = page.getByTestId('brand');
    const actions = page.getByTestId('header-actions');
    await expect(nav).toBeVisible();
    const [n, l, r] = await Promise.all([nav.boundingBox(), brand.boundingBox(), actions.boundingBox()]);
    expect(n && l && r).toBeTruthy();
    expect(Math.abs(n!.x + n!.width / 2 - width / 2)).toBeLessThanOrEqual(1);
    expect(n!.width).toBeCloseTo(276, 0);
    expect(n!.y).toBeCloseTo(15, 0);
    expect(l!.x + l!.width).toBeLessThan(n!.x);
    expect(n!.x + n!.width).toBeLessThan(r!.x);
    await expect(actions.getByRole('link', { name: '外观设置' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('approved mobile capsule keeps its dimensions, safe spacing and four route links', async ({ page }) => {
  for (const [width, height, expectedWidth, expectedHeight] of [[390, 844, 358, 78], [320, 800, 296, 66]]) {
    await page.setViewportSize({ width, height });
    await page.goto('http://127.0.0.1:19309/');
    const nav = page.getByTestId('mobile-nav');
    await expect(nav).toBeVisible();
    const box = await nav.boundingBox();
    expect(box).toBeTruthy();
    expect(box!.width).toBeCloseTo(expectedWidth, 0);
    expect(box!.height).toBeCloseTo(expectedHeight, 0);
    expect(Math.abs(box!.x + box!.width / 2 - width / 2)).toBeLessThanOrEqual(1);
    expect(height - box!.y - box!.height).toBeGreaterThanOrEqual(14);
    const links = nav.getByRole('link');
    await expect(links).toHaveCount(4);
    await nav.getByRole('link', { name: '课程' }).click();
    await expect(page).toHaveURL(/\/courses/);
    await expect(nav.getByRole('link', { name: '课程' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('link', { name: '外观设置' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('system theme updates live and glass responds to reduced transparency', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'night');
  const nav = page.getByTestId('mobile-nav');
  const normalBlur = await nav.evaluate(element => getComputedStyle(element).backdropFilter);
  expect(normalBlur).toContain('blur');
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }, { name: 'prefers-reduced-transparency', value: 'reduce' }] });
  await expect(nav).toHaveCSS('backdrop-filter', 'none');
  await session.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'day');
  await expect(nav).toBeVisible();
});

test('forced-colors preserves visible navigation, selection, and keyboard focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ forcedColors: 'active' });
  await page.goto('http://127.0.0.1:19309/');
  const nav = page.getByTestId('mobile-nav');
  await expect(nav).toBeVisible();
  const current = nav.getByRole('link', { name: '首页' });
  await expect(current).toHaveAttribute('aria-current', 'page');
  const selectedDecoration = await current.evaluate(element => {
    const css = getComputedStyle(element);
    return { outline: css.outlineStyle, border: css.borderStyle, decoration: css.textDecorationLine };
  });
  expect(selectedDecoration.outline !== 'none' || selectedDecoration.border !== 'none' || selectedDecoration.decoration !== 'none').toBe(true);
  await page.keyboard.press('Tab');
  await current.focus();
  await expect(current).toBeFocused();
  expect(await current.evaluate(element => element.matches(':focus-visible'))).toBe(true);
});

test('no-blur fallback supplies a solid navigation surface (simulated unsupported CSS)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:19309/');
  // Chromium supports blur; extract the authored @supports-not rules and apply their
  // own declarations to emulate a browser selecting that fallback branch.
  const fallbackCount = await page.evaluate(() => {
    const selected: string[] = [];
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRuleList;
      try { rules = sheet.cssRules; } catch { continue; }
      for (const rule of Array.from(rules)) {
        if (rule instanceof CSSSupportsRule && rule.conditionText.includes('not') && rule.conditionText.includes('backdrop-filter')) {
          selected.push(...Array.from(rule.cssRules, child => child.cssText));
        }
      }
    }
    const style = document.createElement('style'); style.dataset.testFallback = 'true'; style.textContent = selected.join('\n'); document.head.append(style);
    return selected.length;
  });
  expect(fallbackCount).toBeGreaterThan(0);
  const nav = page.getByTestId('mobile-nav');
  const style = await nav.evaluate(element => ({ background: getComputedStyle(element).backgroundColor, blur: getComputedStyle(element).backdropFilter }));
  expect(style.background).not.toBe('rgba(0, 0, 0, 0)');
  expect(style.blur).toBe('none');
});

test('the component catalogue exposes meaningful states and controls in both themes', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/__dev/catalog');
  for (const [theme, label] of [['day', 'Aurora Day'], ['night', 'Nebula Night']] as const) {
    await page.getByRole('button', { name: label }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    for (const text of ['正在加载', '加载失败', '结果未知', '离线', '版本冲突', '采集：模拟采集中', '上传：失败', '转写：进行中', 'AI：无任务', '同步：已同步']) {
      await expect(page.getByText(text, { exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: '不可用' })).toBeDisabled();
    const input = page.getByRole('textbox', { name: '课程名称' });
    await input.fill('');
    await page.getByRole('button', { name: '检查输入' }).click();
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    const description = await input.getAttribute('aria-describedby');
    expect(description).toBeTruthy();
    await expect(page.locator(`[id="${description}"]`)).toHaveText('请输入课程名称。');
    const trigger = page.getByRole('button', { name: '打开示例对话框' });
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: '确认示例操作' });
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
  }
});

test('event scripts project distinct transcript, upload, AI, deletion and lease states', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  const scripts = page.getByLabel('模拟事件脚本');
  const run = page.getByRole('button', { name: '运行模拟事件' });
  const result = page.getByRole('region', { name: '模拟事件结果' });
  await scripts.selectOption('transcript');
  await run.click();
  await expect(result).toContainText('正式片段：合成的正式片段');
  await expect(result).toContainText('临时片段：无');
  await expect(result).not.toContainText('迟到的旧片段');
  await scripts.selectOption('upload');
  await run.click();
  await expect(result).toContainText('帧确认：已收到');
  await expect(result).toContainText('持久上传：已确认');
  await scripts.selectOption('taskUnknown');
  await run.click();
  await expect(result).toContainText('AI 结果：未知，可能已收费');
  await expect(page.getByRole('button', { name: /重试/ })).toHaveCount(0);
  await scripts.selectOption('deletion');
  await run.click();
  await expect(result).toContainText('原音撤权：已撤权');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
  await scripts.selectOption('leaseLoss');
  await run.click();
  await expect(result).toContainText('采集租约：已失效');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await scripts.selectOption('classroomDeletion');
  await run.click();
  await expect(result).toContainText('课堂撤权：已撤权');
  await expect(result).toContainText('正式片段：无');
  await expect(result).not.toContainText('不应复活');
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await expect(page.getByRole('heading', { name: '模拟课堂已撤权' })).toBeVisible();
  await expect(page.getByText('线性代数 · 特征值与特征向量')).toHaveCount(0);
  await expect(page.getByText('线性代数 · 转写已完成')).toHaveCount(0);
});

test('individual mock events keep temporary transcript and frame acknowledgement distinct from durable results', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  const scripts = page.getByLabel('模拟事件脚本');
  const step = page.getByRole('button', { name: '模拟下一步事件' });
  const result = page.getByRole('region', { name: '模拟事件结果' });
  await scripts.selectOption('transcript');
  await step.click();
  await expect(result).toContainText('正式片段：无');
  await expect(result).toContainText('临时片段：合成的临时片段');
  await step.click();
  await expect(result).toContainText('正式片段：合成的正式片段');
  await expect(result).toContainText('临时片段：无');
  await scripts.selectOption('upload');
  await step.click();
  await expect(result).toContainText('帧确认：已收到');
  await expect(result).toContainText('持久上传：未确认');
  await step.click();
  await expect(result).toContainText('持久上传：已确认');
});

test('revocation destroys account display and mock subscription; B can start cleanly', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await expect(page.getByText('线性代数', { exact: true }).first()).toBeVisible();
  await page.getByLabel('模拟事件脚本').selectOption('transcript');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(page.getByRole('region', { name: '模拟事件结果' })).toContainText('合成的正式片段');
  await page.getByLabel('模拟事件脚本').selectOption('revocation');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(page.getByRole('heading', { name: '模拟会话已撤销' })).toBeVisible();
  await expect(page.getByText('线性代数', { exact: true })).toHaveCount(0);
  await expect(page.getByText('合成的正式片段')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await expect.poll(() => page.evaluate(async () => (await fetch('/api/web/v1/courses')).status)).toBe(401);
  await page.getByRole('button', { name: '进入模拟账号 B' }).click();
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
  await expect(page.getByText('线性代数', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '模拟事件结果' })).toContainText('正式片段：无');
});

test('revocation while A request is in flight cannot restore data after its late response', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mock-test-delay', '1'));
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 连接中')).toBeVisible();
  await page.getByRole('button', { name: '撤销模拟会话' }).click();
  await expect(page.getByRole('heading', { name: '模拟会话已撤销' })).toBeVisible();
  const status = await page.evaluate(async () => (await fetch('/api/web/v1/courses')).status);
  expect(status).toBe(401);
  await page.waitForTimeout(1500);
  await expect(page.getByRole('heading', { name: '模拟会话已撤销' })).toBeVisible();
  await expect(page.getByText('线性代数', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '进入模拟账号 B' }).click();
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
  await expect(page.getByText('线性代数', { exact: true })).toHaveCount(0);
});
