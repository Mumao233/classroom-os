import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:19309';
const A_ID = 'a0000000-0000-4000-8000-000000000001';
const B_ID = 'b0000000-0000-4000-8000-000000000001';
const EXPIRED = 'd0000000-0000-4000-8000-000000000104';
const shots = resolve(process.cwd(), '../../outputs/pr11b/screenshots');
mkdirSync(shots, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(shots, `${name}.png`), fullPage: true });

async function ready(page: Page, path = '/') {
  await page.goto(`${BASE}${path}`);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
}
/** Client-side navigation keeps the in-page Mock backend (a reload would reset it). */
async function spa(page: Page, path: string) {
  await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
}
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }
function adminRequests(page: Page) {
  const seen: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/web/v1/admin')) seen.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  return seen;
}
async function asAdmin(page: Page, path = '/admin/users') {
  await ready(page, '/dashboard');
  await page.getByRole('button', { name: '登录模拟管理员 C' }).click();
  await expect(page.getByRole('link', { name: '外观设置' })).toHaveText('管');
  await spa(page, path);
}
/** Reason (if any) → password + code → done. */
async function verify(page: Page, code = '123456') {
  const dialog = page.getByTestId('admin-action');
  await dialog.getByLabel('当前密码').fill('synthetic-password');
  await dialog.getByLabel('验证器中的 6 位验证码').fill(code);
  await dialog.getByRole('button', { name: '验证并执行' }).click();
}

test.describe('PR-11b operations console', () => {
  test('a student has no operations entry and the admin URLs make no admin request (FE-13, AUTH-05)', async ({ page }) => {
    const seen = adminRequests(page);
    await ready(page, '/dashboard');
    await expect(page.getByRole('link', { name: '运营' })).toHaveCount(0);
    for (const path of ['/admin/users', '/admin/invites', '/admin/usage', '/admin/runtime']) {
      await spa(page, path);
      await expect(page.getByTestId('admin-denied')).toBeVisible();
    }
    await page.getByRole('button', { name: /切换模拟账号/ }).click();
    await spa(page, '/admin/users');
    await expect(page.getByTestId('admin-denied')).toBeVisible();
    // The UI never asked; the API refuses anyway.
    expect(seen).toEqual([]);
    expect(await page.evaluate(async () => (await fetch('/api/web/v1/admin/users')).status)).toBe(403);
    await shot(page, 'admin-denied-student');
  });

  test('users: disable with a reason and a reauthentication; the audit shows it; no content or impersonation entry (AUTH-07)', async ({ page }) => {
    await asAdmin(page);
    await expect(page.getByRole('heading', { name: '用户', level: 1 })).toBeVisible();
    await expect(page.getByTestId('admin-boundary')).toContainText('不能以其他用户身份登录');
    const row = page.getByTestId(`admin-user-${B_ID}`);
    await expect(row).toContainText('student-b@example.invalid');
    // The admin's own row has no status button.
    await expect(page.getByTestId('admin-user-c9000000-0000-4000-8000-000000000001').getByRole('button', { name: '停用' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /登录为|查看笔记|查看课堂/ })).toHaveCount(0);

    await row.getByRole('button', { name: '停用' }).click();
    const dialog = page.getByTestId('admin-action');
    const next = dialog.getByRole('button', { name: '下一步：验证身份' });
    await expect(next).toBeDisabled();
    await dialog.getByLabel('操作理由').fill('合成：学生申请暂停使用');
    await next.click();
    await verify(page, '000000');
    await expect(dialog.getByRole('alert')).toContainText('密码或验证码不正确');
    // The factors are cleared after every attempt.
    await expect(dialog.getByLabel('当前密码')).toHaveValue('');
    await verify(page);
    await expect(dialog).toHaveCount(0);
    await expect(row).toContainText('已停用');
    await expect(row.getByRole('button', { name: '恢复' })).toBeVisible();
    await shot(page, 'admin-users');

    // Only an active account has a quota projection (billing backend rule).
    await expect(row.getByRole('button', { name: /查看 .* 的额度/ })).toHaveCount(0);
    await page.getByTestId(`admin-user-${A_ID}`).getByRole('button', { name: /查看 .* 的额度/ }).click();
    await expect(page.getByTestId('admin-quota')).toContainText('实时转写（本月）');
    await page.getByTestId('admin-quota').getByRole('button', { name: '追加总结与卡片（本月）' }).click();
    await page.getByLabel('追加数量（次）').fill('10');
    await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：期末复习追加');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    await expect(page.getByTestId('admin-action')).toHaveCount(0);
    await expect(page.getByTestId('admin-quota')).toContainText('70 次');

    await page.getByRole('link', { name: '运行状态' }).click();
    const audit = page.getByTestId('admin-audit');
    await expect(audit.locator('tbody tr').first()).toContainText('合成：期末复习追加');
    await expect(audit).toContainText('合成：学生申请暂停使用');
    await expect(audit).toContainText('student-b@example.invalid');
    await expect(audit).not.toContainText('synthetic-password');
  });

  test('a lost reply keeps the same request: retry applies it once (idempotent replay)', async ({ page }) => {
    await asAdmin(page);
    await page.evaluate(id => localStorage.setItem('mock-test-drop-reply', `/admin/users/${id}`), B_ID);
    await page.getByTestId(`admin-user-${B_ID}`).getByRole('button', { name: '停用' }).click();
    await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：响应丢失检查');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    await expect(page.getByTestId('admin-unconfirmed')).toBeVisible();
    await page.getByRole('button', { name: '重试同一操作' }).click();
    await expect(page.getByTestId('admin-action')).toHaveCount(0);
    await expect(page.getByTestId(`admin-user-${B_ID}`)).toContainText('已停用');
    await page.getByRole('link', { name: '运行状态' }).click();
    await expect(page.getByTestId('admin-audit').getByText('合成：响应丢失检查')).toHaveCount(1);
  });

  test('leaving an unconfirmed quota adjustment and submitting it again never applies it twice', async ({ page }) => {
    await asAdmin(page);
    await page.evaluate(() => localStorage.setItem('mock-test-drop-reply', '/admin/quota-adjustments'));
    const submit = async () => {
      await page.getByTestId('admin-quota').getByRole('button', { name: '追加总结与卡片（本月）' }).click();
      await page.getByLabel('追加数量（次）').fill('5');
      await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：重复提交检查');
      await page.getByRole('button', { name: '下一步：验证身份' }).click();
      await verify(page);
    };
    await page.getByTestId(`admin-user-${B_ID}`).getByRole('button', { name: /查看 .* 的额度/ }).click();
    await submit();
    await expect(page.getByTestId('admin-unconfirmed')).toBeVisible();
    await page.getByRole('button', { name: '放弃并刷新' }).click();
    // It was applied once on the server.
    await expect(page.getByTestId('admin-quota')).toContainText('65 次');
    await page.getByTestId('admin-quota').getByRole('button', { name: '追加总结与卡片（本月）' }).click();
    await page.getByLabel('追加数量（次）').fill('5');
    await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：重复提交检查');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    // The earlier identical request is surfaced; replaying it is the safe default.
    await expect(page.getByTestId('admin-earlier-unconfirmed')).toBeVisible();
    await expect(page.getByRole('button', { name: '作为新的操作提交' })).toBeVisible();
    await page.getByRole('button', { name: '重试上一次（不会重复执行）' }).click();
    await verify(page);
    await expect(page.getByTestId('admin-action')).toHaveCount(0);
    await expect(page.getByTestId('admin-quota')).toContainText('65 次');
    await expect(page.getByTestId('admin-quota')).not.toContainText('70 次');
    await page.getByRole('link', { name: '运行状态' }).click();
    await expect(page.getByTestId('admin-audit').getByText('合成：重复提交检查')).toHaveCount(1);
  });

  test('a confirmed write is shown even when the refetch after it cannot reach the server', async ({ page }) => {
    await asAdmin(page);
    await page.evaluate(id => localStorage.setItem('mock-test-offline-after', `/admin/users/${id}`), B_ID);
    await page.getByTestId(`admin-user-${B_ID}`).getByRole('button', { name: '停用' }).click();
    await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：确认后断网');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    await expect(page.getByTestId('admin-action')).toHaveCount(0);
    await expect(page.getByTestId(`admin-user-${B_ID}`)).toContainText('已停用');
  });

  test('invitations: the one-time token is shown once and is gone after dismissal; reissue replaces it', async ({ page }) => {
    const consoleText: string[] = [];
    page.on('console', message => consoleText.push(message.text()));
    await asAdmin(page, '/admin/invites');
    await page.getByRole('button', { name: '创建邀请' }).click();
    await page.getByLabel('受邀邮箱').fill('new-student@example.invalid');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    const panel = page.getByTestId('invite-token');
    await expect(panel).toContainText('只显示这一次');
    const token = await page.getByLabel('一次性令牌').inputValue();
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    await expect(page.getByTestId('admin-invitations')).toContainText('n***@example.invalid');
    // The one-time token is masked in the durable screenshot.
    await page.screenshot({ path: resolve(shots, 'admin-invites-token.png'), fullPage: true, mask: [page.getByLabel('一次性令牌')] });
    await panel.getByRole('button', { name: '我已保存，关闭' }).click();
    await expect(panel).toHaveCount(0);
    expect(await page.content()).not.toContain(token);
    // Nothing of it was persisted by the page.
    expect(await page.evaluate(value => JSON.stringify({ ...localStorage }).includes(value) || JSON.stringify({ ...sessionStorage }).includes(value), token)).toBe(false);
    // An admin write without the session's CSRF token is refused at the boundary.
    expect(await page.evaluate(async () => (await fetch('/api/web/v1/admin/invitations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ kind: 'single_use' }) })).status)).toBe(403);
    // Nor logged (the Mock transport is quiet): neither the token nor a reauthentication header.
    expect(consoleText.join(' ')).not.toContain(token);
    expect(consoleText.join(' ')).not.toContain('X-Reauth-Token');

    await page.getByTestId(`admin-invitation-${EXPIRED}`).getByRole('button', { name: '重新签发' }).click();
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    await expect(page.getByTestId('invite-token')).toContainText('新的一次性令牌');
    await expect(page.getByTestId(`admin-invitation-${EXPIRED}`)).toContainText('未使用');
    // Revoking it while its token is still on screen withdraws the token too.
    await page.getByTestId(`admin-invitation-${EXPIRED}`).getByRole('button', { name: '撤销' }).click();
    await page.getByRole('button', { name: '下一步：验证身份后撤销' }).click();
    await verify(page);
    await expect(page.getByTestId(`admin-invitation-${EXPIRED}`)).toContainText('已撤销');
    await expect(page.getByTestId('invite-token')).toHaveCount(0);
  });

  test('usage shows exact amounts, the risk level and an empty month', async ({ page }) => {
    await asAdmin(page, '/admin/usage');
    const usage = page.getByTestId('admin-usage');
    await expect(usage).toContainText('¥500.00');
    await expect(usage).toContainText('¥304.40');
    await expect(usage).toContainText('已过 60% 提醒线');
    await expect(usage).toContainText('60.88%');
    await shot(page, 'admin-usage');
    await page.getByLabel('月份').selectOption({ label: '2026 年 9 月' });
    await expect(usage).toContainText('¥268.00');
    await page.getByLabel('月份').selectOption({ label: '2026 年 8 月' });
    await expect(page.getByText('这个月没有用量记录')).toBeVisible();
  });

  test('runtime: health warnings, a switch change with reason and reauth, MFA and offline gates', async ({ page }) => {
    await asAdmin(page, '/admin/runtime');
    await expect(page.getByTestId('admin-health')).toContainText('各项读数正常');
    await page.getByRole('button', { name: '处理停滞' }).click();
    await expect(page.getByTestId('admin-health-warnings')).toContainText('后台处理进程已 15 分钟没有心跳');
    await expect(page.getByTestId('admin-health-warnings')).toContainText('2 项删除失败');

    const switches = page.getByTestId('admin-switches');
    await switches.getByLabel(/AI 问答、讲解与总结/).uncheck();
    await switches.getByRole('button', { name: '保存变更' }).click();
    await expect(page.getByTestId('switch-changes')).toContainText('AI 问答、讲解与总结：开 → 关');
    await page.getByTestId('admin-action').getByLabel('操作理由').fill('合成：模型供应商维护');
    await page.getByRole('button', { name: '下一步：验证身份' }).click();
    await verify(page);
    await expect(page.getByTestId('admin-action')).toHaveCount(0);
    await expect(switches.getByLabel(/AI 问答、讲解与总结/)).not.toBeChecked();
    // The active-user stage is shown read-only until stage transitions are approved.
    await expect(page.getByTestId('active-user-stage')).toHaveText('10 人');
    await expect(switches.getByRole('combobox')).toHaveCount(0);
    await expect(page.getByTestId('admin-audit').locator('tbody tr').first()).toContainText('修改平台开关');
    await shot(page, 'admin-runtime');

    await page.getByLabel('模拟离线').check();
    await expect(switches.getByLabel(/实时转写/)).toBeDisabled();
    await page.getByLabel('模拟离线').uncheck();

    await page.getByRole('button', { name: '管理员未启用 MFA' }).click();
    await expect(page.getByTestId('admin-mfa-required')).toBeVisible();
    await expect(page.getByTestId('admin-health')).toHaveCount(0);
    await page.getByRole('button', { name: '管理员已启用 MFA' }).click();
    await expect(page.getByTestId('admin-health')).toBeVisible();
  });

  test('desktop nav has the operations entry for an administrator only', async ({ page }) => {
    await asAdmin(page, '/dashboard');
    await page.getByRole('link', { name: '运营' }).click();
    await expect(page).toHaveURL(`${BASE}/admin/users`);
    await page.getByRole('button', { name: '回到学生 A' }).click();
    await expect(page.getByRole('link', { name: '运营' })).toHaveCount(0);
  });

  test('phone width: reached from settings; tables scroll inside their panel, the page never overflows', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await asAdmin(page, '/settings/profile');
    await page.getByRole('link', { name: '运营后台' }).click();
    await expect(page.getByTestId('admin-users')).toBeVisible();
    await noOverflow(page);
    for (const path of ['/admin/invites', '/admin/usage', '/admin/runtime']) { await spa(page, path); await expect(page.getByRole('heading', { level: 1 })).toBeVisible(); await noOverflow(page); }
    await shot(page, 'admin-runtime-phone');
  });
});
