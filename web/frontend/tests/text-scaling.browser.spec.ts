import { expect, test, type Page } from '@playwright/test';

/** Simulate a text-only user stylesheet in a real browser. Snapshot every computed
 * font before applying it: multiplying inherited sizes recursively would exaggerate
 * scaling, while changing only the root would miss px-based declarations. */
async function doubleRenderedText(page: Page) {
  await page.evaluate(() => {
    const elements = Array.from(document.querySelectorAll<HTMLElement>('body, body *'));
    const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
    elements.forEach((element, index) => {
      if (Number.isFinite(sizes[index])) element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important');
    });
  });
}
async function noDocumentOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test('actual 200% text: dashboard content and mobile navigation remain readable and operable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  const title = page.getByRole('heading', { name: /下午好/ });
  const course = page.getByRole('heading', { name: '线性代数', exact: true });
  const body = page.getByText('为接下来的课堂留好位置。');
  const nav = page.getByRole('navigation', { name: '移动主导航' });
  const label = nav.getByRole('link', { name: '课程' }).locator('span');
  const targets = [title, course, body, label];
  const original = await Promise.all(targets.map(target => target.evaluate(element => parseFloat(getComputedStyle(element).fontSize))));
  await doubleRenderedText(page);
  for (let index = 0; index < targets.length; index++) {
    const size = await targets[index].evaluate(element => parseFloat(getComputedStyle(element).fontSize));
    expect(size).toBeCloseTo(original[index] * 2, 1);
    await expect(targets[index]).toBeVisible();
  }
  await noDocumentOverflow(page);
  for (const name of ['首页', '课程', '课堂', '复习']) {
    const link = nav.getByRole('link', { name });
    await expect(link).toBeVisible();
    const geometry = await link.evaluate(element => {
      const parent = element.getBoundingClientRect(), label = element.querySelector('span')!.getBoundingClientRect();
      return label.left >= parent.left && label.right <= parent.right && label.top >= parent.top && label.bottom <= parent.bottom;
    });
    expect(geometry).toBe(true);
  }
  await nav.getByRole('link', { name: '课程' }).click();
  await expect(page).toHaveURL(/\/courses/);
});

test('actual 200% text: recording stop remains reachable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const stop = page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止' });
  await doubleRenderedText(page);
  await noDocumentOverflow(page);
  await expect(stop).toBeInViewport();
  await stop.click();
  await expect(stop).toHaveCount(0);
});

test('actual 200% text: dialog, long course name, formula and actions remain usable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await page.getByRole('link', { name: /组件目录/ }).click();
  const title = page.getByTestId('long-course-name');
  const titleSize = await title.evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  await page.getByRole('button', { name: '打开示例对话框' }).click();
  const dialog = page.getByRole('dialog', { name: '确认示例操作' });
  const dialogTitle = dialog.getByRole('heading', { name: '确认示例操作' });
  const dialogSize = await dialogTitle.evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  await doubleRenderedText(page);
  expect(await title.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeCloseTo(titleSize * 2, 1);
  expect(await dialogTitle.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeCloseTo(dialogSize * 2, 1);
  await expect(dialog).toBeInViewport();
  const stop = dialog.getByRole('button', { name: '停止模拟课堂' });
  await stop.scrollIntoViewIfNeeded();
  await expect(stop).toBeInViewport();
  await stop.click();
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await dialog.getByRole('button', { name: '知道了' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(title).toBeVisible();
  await noDocumentOverflow(page);
  const formula = page.getByRole('region', { name: '长公式示例' });
  expect(await formula.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  await formula.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => formula.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  await page.getByRole('button', { name: '主要操作' }).click();
});
