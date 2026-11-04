import { expect, test } from '@playwright/test';

test('the page commands and synthetic events use the runtime capture, upload and event ports', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  await expect(result).toHaveAttribute('data-port-capture', 'idle');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toContainText('采集 · 录音中');
  await page.getByLabel('模拟事件脚本').selectOption('upload');
  await page.getByRole('button', { name: '模拟下一步事件' }).click();
  await expect(result).toHaveAttribute('data-port-upload-pending', 'true');
  await expect(result).toContainText('帧确认：已收到');
  await expect(result).toContainText('持久上传：未确认');
  await page.getByRole('button', { name: '模拟下一步事件' }).click();
  await expect(result).toHaveAttribute('data-port-upload-pending', 'false');
  await expect(result).toContainText('持久上传：已确认');
  await page.getByRole('region', { name: '模拟课堂状态' }).getByRole('button', { name: '停止' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'ended');
  await page.getByRole('button', { name: '切换模拟账号 A' }).click();
  await expect(page.getByText('组合数学（模拟账号 B）')).toBeVisible();
  await expect(result).toHaveAttribute('data-port-capture', 'idle');
  // Account B owns a newly staged synthetic chunk, not A's durable receipt.
  await expect(result).toHaveAttribute('data-port-upload-pending', 'true');
  await expect(result).toContainText('帧确认：未收到');
  await expect(result).toContainText('持久上传：未确认');
});
