import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Account-shell revocation on receipt (REVOCATION-DESIGN.md). Every deletion below loses
 * connectivity right after its reply ("mock-test-offline-after"), so no refetch can succeed: what
 * disappears is removed by the receipt itself, not by a re-read.
 */
const BASE = 'http://127.0.0.1:19309';
const FALL = '11111111-1111-4111-8111-111111111111';
const LA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1';
const MP = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3';
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const SUMMARY = 'e0000000-0000-4000-8000-000000000001';
const SUMMARY_TITLE = '特征值与特征向量 · 课堂总结';
const shots = resolve(process.cwd(), '../../outputs/revocation/screenshots');
mkdirSync(shots, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(shots, `${name}.png`), fullPage: true });

async function ready(page: Page, path = '/') {
  await page.goto(`${BASE}${path}`);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
}
/** Client-side navigation keeps the in-page Mock backend, runtime and query cache. */
async function spa(page: Page, path: string) {
  await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
}
const testControl = (page: Page, name: string, value: string) => page.evaluate(([key, item]) => localStorage.setItem(key!, item!), [name, value]);
/** The transport fails every request from now on (the page is not told: a silent loss). */
const loseConnectionAfterDelete = (page: Page) => testControl(page, 'mock-test-offline-after', '/delete');
async function deleteCourse(page: Page, courseId: string) {
  await spa(page, `/courses/${courseId}/overview`);
  await page.getByRole('button', { name: '删除…', exact: true }).click();
  await expect(page.getByTestId('delete-impact')).toBeVisible();
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(page).toHaveURL(/\/courses$/);
}
async function cacheReviewDocument(page: Page) {
  await spa(page, `/review/items/${SUMMARY}`);
  await expect(page.getByTestId('reader')).toContainText('特征值');
}

test.describe('revocation on receipt (offline after the reply)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => { for (const key of ['mock-test-offline-after', 'mock-test-delay-path', 'mock-test-fail-get']) localStorage.removeItem(key); });
  });

  test('scenario 1 · switching to "move" while a cascade is in flight is locked and the cascade is still revoked', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(page.getByTestId('reader')).toContainText('特征值');
    await spa(page, `/courses/${LA}/overview`);
    await page.getByRole('button', { name: '删除…', exact: true }).click();
    await expect(page.getByTestId('delete-impact')).toBeVisible();
    await testControl(page, 'mock-test-delay-path', '/delete');
    await loseConnectionAfterDelete(page);
    const move = page.getByRole('radio', { name: /先把课堂移到其他课程/ });
    const cascade = page.getByRole('radio', { name: /连同其中的课堂一并删除/ });
    await expect(cascade).toBeChecked();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page.getByRole('button', { name: '正在删除…' })).toBeVisible();
    // While the request is pending, the mode and destination cannot change.
    await expect(move).toBeDisabled();
    await expect(cascade).toBeDisabled();
    await move.click({ force: true });
    await expect(cascade).toBeChecked();
    await expect(page).toHaveURL(/\/courses$/);
    await spa(page, `/review/items/${SUMMARY}`);
    await expect(page.getByRole('alert')).toContainText('内容已被删除');
    await expect(page.getByTestId('reader')).toHaveCount(0);
    await expect(page.getByText(SUMMARY_TITLE)).toHaveCount(0);
    await shot(page, 'scenario1-cascade-locked');
  });

  test('scenario 2 · a confirmed cascade stops the bound capture even though the connection is lost right after', async ({ page }) => {
    await ready(page);
    await spa(page, '/classrooms');
    await page.getByRole('combobox', { name: '课程' }).selectOption({ label: '大学物理 II' });
    await page.getByLabel('课堂标题').fill('断网前被级联删除');
    await page.getByRole('button', { name: '创建并进入准备' }).click();
    await expect(page.getByTestId('classroom-prep')).toBeVisible();
    const classId = /classrooms\/([0-9a-f-]{36})\/live/.exec(page.url())![1]!;
    await page.getByLabel(/我已了解以上用途/).check();
    await page.getByRole('button', { name: '开始采集' }).click();
    await expect(page.getByTestId('capture-phase')).toHaveText('采集中');
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toBeVisible();
    await loseConnectionAfterDelete(page);
    await deleteCourse(page, MP);
    await expect(page.getByRole('region', { name: '模拟课堂状态' })).toHaveCount(0);
    // The capture's unconfirmed chunk can no longer be accepted for the revoked classroom.
    await expect(page.getByRole('region', { name: '模拟上传历史' }).locator('li', { hasText: '当前采集' })).toContainText('已失效，无法确认');
    await spa(page, `/classrooms/${classId}/live`);
    await expect(page.getByTestId('classroom-prep')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '断网前被级联删除' })).toHaveCount(0);
    await shot(page, 'scenario2-capture-revoked');
  });

  test('scenario 3 · a semester cascade drops review entries whose classroom membership was never read', async ({ page }) => {
    // The classroom lists never load, so no cached data says which course the documents' classrooms belong to.
    await page.addInitScript(() => localStorage.setItem('mock-test-fail-get', '/class-sessions'));
    await ready(page, '/review');
    await expect(page.getByTestId('library-list')).toContainText(SUMMARY_TITLE);
    await spa(page, `/semesters/${FALL}/edit`);
    await page.getByRole('button', { name: '删除学期…' }).click();
    await expect(page.getByTestId('delete-impact')).toBeVisible();
    await loseConnectionAfterDelete(page);
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page.getByTestId('delete-dialog')).toHaveCount(0);
    await spa(page, '/review');
    await expect(page.getByRole('heading', { name: '复习', level: 1 })).toBeVisible();
    await expect(page.getByText(SUMMARY_TITLE)).toHaveCount(0);
    await expect(page.getByText('红黑树的插入 · 候选总结')).toHaveCount(0);
    await shot(page, 'scenario3-unknown-membership');
  });

  test('scenario 4 · an authoritative denial hides the cached revision bodies', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(page.getByTestId('reader')).toContainText('特征值');
    await page.evaluate(id => (globalThis as unknown as { __classroomMock: { denyRevisions(id: string): void } }).__classroomMock.denyRevisions(id), SUMMARY);
    // Any write re-reads the account's projections; the revisions read is now FORBIDDEN.
    await page.getByTestId('toggle-understood').click();
    await expect(page.getByTestId('document-state')).toContainText('已理解');
    await expect(page.getByTestId('reader')).toHaveCount(0);
    await expect(page.getByText('当前账号无权执行此操作。')).toBeVisible();
    await shot(page, 'scenario4-denied-revisions');
  });

  test('scenario 4 · a transport failure keeps the loaded document readable', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(page.getByTestId('reader')).toContainText('特征值');
    await testControl(page, 'mock-test-offline-after', `/review-documents/${SUMMARY}`);
    await page.getByTestId('toggle-understood').click();
    await expect(page.getByTestId('document-state')).toContainText('已理解');
    // The re-read after the write fails at the transport: the content stays, with a warning.
    await expect(page.getByTestId('document-refresh-error')).toContainText('无法连接服务');
    await expect(page.getByTestId('reader')).toContainText('特征值');
    // The fallback is read-only: no server write is offered until a refresh succeeds.
    await expect(page.getByTestId('toggle-understood')).toBeDisabled();
    await expect(page.getByTestId('edit-personal')).toBeDisabled();
  });

  test('scenario 5 · one receipt revokes the course everywhere: dashboard, course, classroom record, classroom review and review pages', async ({ page }) => {
    await ready(page, '/dashboard');
    await expect(page.getByTestId('dashboard')).toContainText('特征值与特征向量');
    await spa(page, `/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toBeVisible();
    await spa(page, `/classrooms/${ENDED}/record`);
    await expect(page.getByRole('heading', { name: '特征值与特征向量', level: 1 })).toBeVisible();
    await spa(page, `/classrooms/${ENDED}/review`);
    await expect(page.getByRole('heading', { name: '特征值与特征向量', level: 1 })).toBeVisible();
    await cacheReviewDocument(page);
    await spa(page, '/review');
    await expect(page.getByTestId('library-list')).toContainText(SUMMARY_TITLE);
    await loseConnectionAfterDelete(page);
    await deleteCourse(page, LA);

    await spa(page, '/dashboard');
    await expect(page.getByTestId('dashboard')).not.toContainText('特征值与特征向量');
    await spa(page, `/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '课程不存在或已删除' })).toBeVisible();
    await spa(page, `/classrooms/${ENDED}/record`);
    await expect(page.getByRole('alert')).toContainText('内容已被删除');
    await expect(page.getByRole('heading', { name: '特征值与特征向量' })).toHaveCount(0);
    await spa(page, `/classrooms/${ENDED}/review`);
    await expect(page.getByRole('heading', { name: '特征值与特征向量' })).toHaveCount(0);
    await spa(page, `/review/items/${SUMMARY}`);
    await expect(page.getByTestId('reader')).toHaveCount(0);
    await spa(page, '/review');
    await expect(page.getByText(SUMMARY_TITLE)).toHaveCount(0);
    await shot(page, 'scenario5-review-after-cascade');
  });

  test('session revocation while offline clears every cached page', async ({ page }) => {
    await ready(page, `/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toBeVisible();
    await page.getByLabel('模拟离线').check();
    await page.getByRole('button', { name: '撤销模拟会话' }).click();
    await expect(page.getByRole('heading', { name: '模拟会话已撤销' })).toBeVisible();
    await page.getByRole('button', { name: '进入模拟账号 A' }).click();
    await spa(page, `/courses/${LA}/overview`);
    await expect(page.getByRole('heading', { name: '线性代数', level: 1 })).toHaveCount(0);
  });
});
