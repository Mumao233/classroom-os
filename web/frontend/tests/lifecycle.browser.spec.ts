import { expect, test, type Page } from '@playwright/test';

async function startFromDashboard(page: Page) {
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
}

test('two capture lifecycles show only their own upload acknowledgement', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await expect(result).toHaveAttribute('data-port-upload-pending', 'true');
  await expect(bar).toContainText('上传 · 待处理');
  await page.getByLabel('模拟事件脚本').selectOption('upload');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(result).toContainText('持久上传：已确认');
  await expect(bar).toContainText('上传 · 已确认');
  await bar.getByRole('button', { name: '停止' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'ended');
  await startFromDashboard(page);
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await expect(result).toHaveAttribute('data-port-upload-pending', 'true');
  await expect(result).toContainText('帧确认：未收到');
  await expect(result).toContainText('持久上传：未确认');
  await expect(bar).toContainText('上传 · 待处理');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(result).toContainText('持久上传：已确认');
  await expect(bar).toContainText('上传 · 已确认');
});

test('both renewed lease lifecycles accept their own durable and lease-loss events', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  for (let round = 0; round < 2; round++) {
    await page.getByLabel('模拟事件脚本').selectOption('upload');
    await page.getByRole('button', { name: '运行模拟事件' }).click();
    await expect(result).toContainText('持久上传：已确认');
    await page.getByLabel('模拟事件脚本').selectOption('leaseLoss');
    await page.getByRole('button', { name: '运行模拟事件' }).click();
    await expect(result).toHaveAttribute('data-port-capture', 'failed');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    if (round === 0) {
      await page.getByRole('button', { name: '重新获取模拟租约' }).click();
      await startFromDashboard(page);
      await expect(result).toHaveAttribute('data-port-capture', 'recording');
      await expect(result).toContainText('持久上传：未确认');
    }
  }
});

test('mock Vault results shown on the page track writes, locking, failure and account switch', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('05');
  const panel = page.getByRole('complementary', { name: '开发模拟控制' });
  const bar = page.getByRole('region', { name: '模拟课堂状态' });
  await expect(panel.getByText('模拟 Vault 记录：无')).toBeVisible();
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  const record = panel.locator('p').filter({ hasText: '模拟 Vault 记录：' });
  await expect(record).not.toHaveText('模拟 Vault 记录：无');
  await expect(record).toContainText('"account":"A"');
  await expect(record).toContainText('"kind":"synthetic-only"');
  await expect(bar).toContainText('仅模拟本地保存');
  const savedRecord = await record.textContent();
  await bar.getByRole('button', { name: '停止' }).click();
  await startFromDashboard(page);
  await expect(bar).toContainText('仅模拟本地保存');
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  await expect(record).toHaveText(savedRecord!);
  await page.getByRole('button', { name: '锁定模拟 Vault' }).click();
  await expect(panel.getByText('模拟 Vault 已锁定，无法写入。')).toBeVisible();
  await expect(record).toHaveText('模拟 Vault 记录：已锁定，无法读取');
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  await expect(record).toHaveText(savedRecord!);
  await page.getByRole('button', { name: '模拟写入失败' }).click();
  await expect(panel.getByText('尚未安全保存，仅在当前页面。请处理后再离开。')).toBeVisible();
  await expect(bar).toContainText('未安全保存');
  await expect(record).toHaveText(savedRecord!);
  await page.getByRole('button', { name: '锁定模拟 Vault' }).click();
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  await expect(panel.getByText('模拟 Vault 已锁定，无法写入。')).toBeVisible();
  await expect(bar).toContainText('未安全保存');
  await page.getByRole('button', { name: '解锁模拟 Vault' }).click();
  await page.getByRole('button', { name: '模拟写入成功' }).click();
  await expect(panel.getByText('模拟本地保存成功，等待联网核对。')).toBeVisible();
  await page.getByRole('button', { name: '切换模拟账号 A' }).click();
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
  await page.getByLabel('模拟场景').selectOption('05');
  await expect(page.getByText('模拟 Vault 记录：无')).toBeVisible();
  await page.getByRole('button', { name: '撤销模拟会话' }).click();
  await page.getByRole('button', { name: '进入模拟账号 A' }).click();
  await page.getByLabel('模拟场景').selectOption('05');
  await expect(page.getByText('模拟 Vault 记录：无')).toBeVisible();
});
