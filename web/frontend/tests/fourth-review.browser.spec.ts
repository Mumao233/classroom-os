import { expect, test, type Page } from '@playwright/test';

async function startFromDashboard(page: Page) {
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
}

test('repeated unlock and scene changes preserve the saved record and pending reconciliation', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('05');
  const panel = page.getByRole('complementary', { name: '开发模拟控制' });
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  const record = panel.locator('p').filter({ hasText: '模拟 Vault 记录：' });
  const before = await record.textContent();
  expect(before).toContain('"account":"A"');
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await expect(record).toHaveText(before!);
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('02');
  await page.getByLabel('模拟场景').selectOption('05');
  await expect(record).toHaveText(before!);
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('02');
  await startFromDashboard(page);
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await expect(bar).toContainText('同步 · 仅模拟本地保存');
  await expect(bar).not.toContainText('同步 · 已同步');
});

test('late durable updates its historical chunk while the new capture remains pending', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  const history = page.getByRole('region', { name: '模拟上传历史' });
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await page.getByLabel('模拟事件脚本').selectOption('upload');
  await page.getByRole('button', { name: '模拟下一步事件' }).click();
  await expect(result).toContainText('帧确认：已收到');
  await expect(result).toContainText('持久上传：未确认');
  const first = history.locator('li').first();
  const firstChunk = await first.getAttribute('data-chunk-id');
  const firstEpoch = await first.getAttribute('data-capture-epoch');
  await expect(first).toHaveAttribute('data-status', 'pending');
  await bar.getByRole('button', { name: '停止' }).click();
  await startFromDashboard(page);
  const second = history.locator('li').last();
  expect(await second.getAttribute('data-chunk-id')).not.toBe(firstChunk);
  expect(await second.getAttribute('data-capture-epoch')).not.toBe(firstEpoch);
  await expect(first).toHaveAttribute('data-status', 'pending');
  await expect(second).toHaveAttribute('data-status', 'pending');
  await expect(first).toContainText('分片 1');
  await expect(second).toContainText('分片 1');
  await first.getByRole('button', { name: '模拟迟到持久回执' }).click();
  await expect(first).toHaveAttribute('data-status', 'durable');
  await expect(second).toHaveAttribute('data-status', 'pending');
  await expect(result).toContainText('持久上传：未确认');
  await expect(result).toHaveAttribute('data-port-upload-pending', 'true');
  await expect(bar).toContainText('上传 · 待处理');
});

test('a revoked lease does not offer an invalid historical receipt action', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await page.getByLabel('模拟事件脚本').selectOption('leaseLoss');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await page.getByRole('button', { name: '重新获取模拟租约' }).click();
  const first = page.getByRole('region', { name: '模拟上传历史' }).locator('li').first();
  await expect(first).toContainText('已失效，无法确认');
  await expect(first.getByRole('button', { name: '模拟迟到持久回执' })).toHaveCount(0);
});
