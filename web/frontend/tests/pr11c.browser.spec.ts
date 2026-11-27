import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:19309';
// Synthetic fixtures of the development workbench (src/mock/backend.ts).
const RED_BLACK = 'c0000000-0000-4000-8000-000000000004';
const PHYSICS = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3';
const PERSONAL_NOTE = 'e0000000-0000-4000-8000-000000000003';
const shots = resolve(process.cwd(), '../../outputs/pr11c/screenshots');
mkdirSync(shots, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(shots, `${name}.png`), fullPage: true });

async function ready(page: Page, path = '/dashboard') {
  await page.goto(`${BASE}${path}`);
  await expect(page.getByText('Mock API 已连接')).toBeVisible();
}
/** Client-side navigation keeps the in-page Mock backend (a reload would reset it). */
async function spa(page: Page, path: string) {
  await page.evaluate(target => { history.pushState(null, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
}
function trackModelRequests(page: Page) {
  const seen: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/\/(generation-plans|questions|tasks\/[^/]+\/retry)/.test(path) && request.method() !== 'GET') seen.push(path);
  });
  return seen;
}

test.use({ trace: 'off' });

test.describe('PR-11c search, notifications and feedback', () => {
  test('Ctrl+K opens keyword search, opens a re-authorised hit and returns focus on Escape (UX-03)', async ({ page }) => {
    const models = trackModelRequests(page);
    await ready(page);
    const trigger = page.getByRole('link', { name: '搜索' });
    await trigger.focus();
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: '搜索' });
    await expect(palette).toBeVisible();
    await expect(palette.getByRole('searchbox')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await page.keyboard.press('Control+k');
    await palette.getByRole('searchbox').fill('大学物理');
    await palette.getByRole('button', { name: '搜索', exact: true }).click();
    await shot(page, 'palette-results');
    await palette.getByRole('button', { name: '大学物理 II' }).click();
    await expect(page).toHaveURL(`${BASE}/courses/${PHYSICS}/overview`);
    await expect(palette).toHaveCount(0);
    // Keyword search never starts a model request (UI-REVIEW-10).
    expect(models).toEqual([]);
  });

  test('a search address runs once, opens a classroom record, and drops a hit deleted meanwhile (FE-12)', async ({ page }) => {
    await ready(page);
    await spa(page, '/search?q=' + encodeURIComponent('红黑树'));
    const hit = page.getByRole('button', { name: '红黑树的插入' }).first();
    await expect(hit).toBeVisible();
    await hit.click();
    await expect(page).toHaveURL(new RegExp(`/classrooms/${RED_BLACK}/record$`));

    // Completed (not aborted) search requests: the address is searched exactly once per visit.
    const finished: string[] = [];
    page.on('requestfinished', request => { if (new URL(request.url()).pathname === '/api/web/v1/search') finished.push(request.url()); });
    await spa(page, '/search?q=' + encodeURIComponent('红黑树'));
    await expect(page.getByRole('button', { name: '红黑树的插入' }).first()).toBeVisible();
    await page.waitForTimeout(500);
    expect(finished).toHaveLength(1);
    // Deleted elsewhere after the search: opening re-reads the classroom and removes the stale hit.
    await page.evaluate(id => (globalThis as unknown as { __classroomMock: { revokeClassroom: (id: string) => boolean } }).__classroomMock.revokeClassroom(id), RED_BLACK);
    await page.getByRole('button', { name: '红黑树的插入' }).first().click();
    await expect(page.getByText('这条结果已无法访问，已从列表移除。')).toBeVisible();
    await expect(page).toHaveURL(/\/search/);
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.getByRole('button', { name: '红黑树的插入' })).toHaveCount(0);
    await shot(page, 'search-tombstone');
  });

  test('an account switch clears results, and the other account cannot find them (AUTH-05)', async ({ page }) => {
    await ready(page);
    await spa(page, '/search?q=' + encodeURIComponent('线性代数'));
    await expect(page.getByRole('button', { name: '线性代数' }).first()).toBeVisible();
    const searches: string[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/web/v1/search') searches.push(request.url()); });
    await page.getByRole('button', { name: /切换模拟账号/ }).click();
    await expect(page.getByRole('link', { name: '外观设置' })).toHaveText('陈');
    await expect(page).toHaveURL(`${BASE}/dashboard`);
    await spa(page, '/search');
    await expect(page.getByRole('searchbox')).toHaveValue('');
    await expect(page.getByRole('list', { name: '搜索结果' })).toHaveCount(0);
    // The next account never searched the previous account's keyword on its own.
    expect(searches).toEqual([]);
    await page.getByRole('searchbox').fill('线性代数');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.getByText('没有匹配的结果。')).toBeVisible();
    await page.getByRole('searchbox').fill('鸽巢');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.getByRole('button', { name: '鸽巢原理' })).toBeVisible();
  });

  test('offline search states its incomplete scope and sends no search request', async ({ page }) => {
    const searches: string[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/web/v1/search') searches.push(request.url()); });
    await ready(page);
    await page.getByLabel('模拟离线').check();
    await spa(page, '/search?q=' + encodeURIComponent('线性代数'));
    await expect(page.getByText('离线期间无法搜索，搜索范围不完整。')).toBeVisible();
    expect(searches).toEqual([]);
  });

  test('marking read keeps an unresolved fault pending, and a completed task is announced (UI-REVIEW-12)', async ({ page }) => {
    await ready(page);
    await spa(page, '/notifications');
    const list = page.getByRole('list', { name: '通知列表' });
    await expect(list.getByRole('listitem')).toHaveCount(3);
    // The conflict notice was read earlier but is not resolved: it stays visible and pending.
    await expect(list.getByText('待处理').first()).toBeVisible();
    const reads: string[] = [];
    page.on('request', request => { if (/\/notifications\/[^/]+\/read$/.test(new URL(request.url()).pathname)) reads.push(request.postData() ?? ''); });
    await list.getByRole('button', { name: '标为已读' }).first().click();
    await expect(list.getByRole('button', { name: '标为已读' })).toHaveCount(1);
    expect(reads).toEqual(['{"read":true}']);
    await shot(page, 'notifications');

    // The unknown-outcome summary task of the 电磁感应 classroom is reconciled as succeeded: one notice,
    // announced as notification.created and shown by the open centre without leaving it.
    await page.evaluate(() => (globalThis as unknown as { __classroomMock: { advanceTask: (id: string, outcome: string) => unknown } })
      .__classroomMock.advanceTask('78000000-0000-4000-8000-000000000001', 'succeeded'));
    await expect(list.getByText('任务已完成')).toBeVisible();
    // Seeded notices name what they are about.
    await expect(list.getByText('课堂原音即将到期')).toBeVisible();
    await expect(list.getByRole('listitem')).toHaveCount(4);
  });

  test('feedback sends no excerpt by default and confirms submission', async ({ page }) => {
    const bodies: unknown[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/web/v1/feedback') bodies.push(request.postDataJSON()); });
    await ready(page);
    await page.getByRole('link', { name: '问题反馈' }).click();
    await expect(page.getByRole('heading', { name: '问题反馈' })).toBeVisible();
    await page.getByLabel('问题说明').fill('合成问题：搜索结果打开后没有定位。');
    await page.getByRole('button', { name: '提交反馈' }).click();
    await expect(page.getByText('反馈已提交。')).toBeVisible();
    expect(bodies).toEqual([expect.objectContaining({ confirmed_excerpt: null, confirm_send_excerpt: false, description: '合成问题：搜索结果打开后没有定位。' })]);
  });

  test('a selected excerpt is offered with consent, sent only when confirmed, and never reaches another account', async ({ page }) => {
    const bodies: unknown[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/web/v1/feedback') bodies.push(request.postDataJSON()); });
    await ready(page);
    await spa(page, `/review/items/${PERSONAL_NOTE}`);
    const phrase = '对角化的前提';
    await expect(page.getByText(phrase, { exact: false }).first()).toBeVisible();
    await page.evaluate(text => {
      const walker = document.createTreeWalker(document.querySelector('main')!, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.textContent!.indexOf(text);
        if (at < 0) continue;
        const range = document.createRange();
        range.setStart(node, at); range.setEnd(node, at + text.length);
        getSelection()!.removeAllRanges(); getSelection()!.addRange(range);
        return;
      }
    }, phrase);
    await page.getByRole('button', { name: /反馈问题/ }).click();
    await expect(page.getByRole('heading', { name: '问题反馈' })).toBeVisible();
    const consent = page.getByLabel('确认随反馈发送此片段');
    await expect(consent).not.toBeChecked();
    await expect(page.getByText(phrase, { exact: true })).toBeVisible();
    // The address history holds only a reference: another account returning to it finds no excerpt.
    await page.getByRole('button', { name: /切换模拟账号/ }).click();
    await expect(page.getByRole('link', { name: '外观设置' })).toHaveText('陈');
    await page.goBack();
    await expect(page.getByRole('heading', { name: '问题反馈' })).toBeVisible();
    await expect(page.getByLabel('确认随反馈发送此片段')).toHaveCount(0);
    await page.getByRole('button', { name: /切换模拟账号/ }).click();
    await expect(page.getByRole('link', { name: '外观设置' })).toHaveText('林');
    await spa(page, `/review/items/${PERSONAL_NOTE}`);
    await expect(page.getByText(phrase, { exact: false }).first()).toBeVisible();
    await page.evaluate(text => {
      const walker = document.createTreeWalker(document.querySelector('main')!, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.textContent!.indexOf(text);
        if (at < 0) continue;
        const range = document.createRange();
        range.setStart(node, at); range.setEnd(node, at + text.length);
        getSelection()!.removeAllRanges(); getSelection()!.addRange(range);
        return;
      }
    }, phrase);
    await page.getByRole('button', { name: /反馈问题/ }).click();
    await page.getByLabel('确认随反馈发送此片段').check();
    await page.getByLabel('问题说明').fill('合成问题：这一段的公式显示不完整。');
    await page.getByRole('button', { name: '提交反馈' }).click();
    await expect(page.getByText('反馈已提交。')).toBeVisible();
    expect(bodies).toEqual([expect.objectContaining({ confirmed_excerpt: phrase, confirm_send_excerpt: true })]);
  });
});
