import { expect, test, type Page } from '@playwright/test';

async function startFromDashboard(page: Page) {
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
}

test('a failed new epoch cannot inherit an old reconciled Vault record after unlock and scene changes', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('05');
  const panel = page.getByRole('complementary', { name: '开发模拟控制' });
  const record = panel.locator('p').filter({ hasText: '模拟 Vault 记录：' });
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  const history = page.getByRole('region', { name: '模拟上传历史' });

  await page.getByRole('button', { name: '模拟写入成功' }).click();
  const originalText = await record.textContent();
  expect(originalText).toContain('"account":"A"');
  const oldRecord = JSON.parse(originalText!.replace('模拟 Vault 记录：', '')) as { captureEpochId: string };
  expect(oldRecord.captureEpochId).toBeTruthy();
  await page.getByRole('button', { name: '模拟联网核对' }).click();
  await expect(bar).toContainText('同步 · 已同步');
  await bar.getByRole('button', { name: '停止' }).click();
  await startFromDashboard(page);
  const failedEpoch = await history.locator('li').last().getAttribute('data-capture-epoch');
  expect(failedEpoch).toBeTruthy();
  expect(failedEpoch).not.toBe(oldRecord.captureEpochId);

  await page.getByRole('button', { name: '锁定模拟 Vault' }).click();
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  await expect(bar).toContainText('同步 · 未安全保存');
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await expect(record).toHaveText(originalText!);
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await page.getByLabel('模拟场景').selectOption('02');
  await startFromDashboard(page);
  await expect(bar).toContainText('同步 · 未安全保存');
  await expect(bar).not.toContainText('同步 · 已同步');

  await page.getByLabel('模拟场景').selectOption('05');
  await expect(record).toHaveText(originalText!);
  await expect(panel.getByText('最近一次写入因 Vault 锁定被拒绝，尚未安全保存。')).toBeVisible();
  const retryEpoch = await history.locator('li').last().getAttribute('data-capture-epoch');
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  const newText = await record.textContent();
  const newRecord = JSON.parse(newText!.replace('模拟 Vault 记录：', '')) as { captureEpochId: string };
  expect(newRecord.captureEpochId).toBe(retryEpoch);
  expect(newRecord.captureEpochId).not.toBe(oldRecord.captureEpochId);
  await expect(bar).toContainText('同步 · 仅模拟本地保存');
  const recordHistory = page.getByRole('region', { name: '模拟 Vault 保存历史' });
  await expect(recordHistory.locator(`li[data-capture-epoch="${oldRecord.captureEpochId}"]`)).toHaveAttribute('data-sync', 'reconciled');
  await expect(recordHistory.locator(`li[data-capture-epoch="${newRecord.captureEpochId}"]`)).toHaveAttribute('data-sync', 'pending');
  await page.getByRole('button', { name: '模拟联网核对' }).click();
  await expect(bar).toContainText('同步 · 已同步');
  await expect(recordHistory.locator(`li[data-capture-epoch="${newRecord.captureEpochId}"]`)).toHaveAttribute('data-sync', 'reconciled');
});
