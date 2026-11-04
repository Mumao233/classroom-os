import { expect, test } from '@playwright/test';

test('classroom revocation stops the real mock capture port and removes the invalid classroom entry', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await page.getByLabel('模拟事件脚本').selectOption('classroomDeletion');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'failed');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await expect(page.getByRole('heading', { name: '模拟课堂已撤权' })).toBeVisible();
  await expect(page.getByRole('button', { name: '开始模拟课堂' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重新获取模拟租约' })).toHaveCount(0);
});

test('expired lease rejects restart until an explicitly renewed lease is acquired', async ({ page }) => {
  await page.goto('http://127.0.0.1:19309/');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  const result = page.getByRole('region', { name: '模拟事件结果' });
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await page.getByLabel('模拟事件脚本').selectOption('leaseLoss');
  await page.getByRole('button', { name: '运行模拟事件' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'failed');
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '首页' }).click();
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'failed');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
  await page.getByRole('button', { name: '重新获取模拟租约' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'idle');
  await page.getByRole('button', { name: '开始模拟课堂' }).click();
  await expect(result).toHaveAttribute('data-port-capture', 'recording');
  await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
});
