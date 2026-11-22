import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = 'http://127.0.0.1:19309';
const LA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1';
const ENDED = 'c0000000-0000-4000-8000-000000000001';
const SUMMARY = 'e0000000-0000-4000-8000-000000000001';
const CARDS = 'e0000000-0000-4000-8000-000000000002';
const NOTE = 'e0000000-0000-4000-8000-000000000003';
const ADOPTED = 'e1000000-0000-4000-8000-000000000001';
const CANDIDATE = 'e1000000-0000-4000-8000-000000000005';
const shots = resolve(process.cwd(), '../../outputs/pr10b/screenshots');
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
async function doubleText(page: Page) {
  await page.evaluate(() => {
    const elements = Array.from(document.querySelectorAll<HTMLElement>('body, body *'));
    const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
    elements.forEach((element, index) => { if (Number.isFinite(sizes[index])) element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important'); });
  });
}
/** A write by "another device" through the same Mock API (a stale base_revision follows). */
async function writeElsewhere(page: Page, path: string, method: 'PATCH' | 'POST', body: unknown) {
  return page.evaluate(async ({ path, method, body }) => (await fetch(`/api/web/v1${path}`, { method, headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(body) })).status, { path, method, body });
}
const versionRow = (page: Page, id: string) => page.getByTestId('version-list').locator(`li[data-revision-id="${id}"]`);

test.describe('PR-10b review', () => {
  test('three entries open the same document; library filters live in the URL and survive the way back (UI-REVIEW-01/02/03)', async ({ page }) => {
    await ready(page, '/review');
    await expect(page.getByRole('heading', { name: '复习', level: 1 })).toBeVisible();
    await expect(page.getByTestId('pending-list')).toContainText('有 1 个新的 AI 候选待比较');
    await expect(page.getByTestId('unresolved-list')).toContainText('为什么特征向量不唯一？');
    await page.locator('#lib-course').selectOption(LA);
    await page.locator('#lib-kind').selectOption('summary');
    await expect(page).toHaveURL(new RegExp(`course_id=${LA}.*kind=summary|kind=summary.*course_id=${LA}`));
    await expect(page.getByTestId('library-list').locator('li')).toHaveCount(1);
    const globalId = await page.getByTestId('library-list').locator('li').first().getAttribute('data-document-id');
    await page.getByTestId('library-list').getByRole('link', { name: '打开' }).click();
    await expect(page).toHaveURL(`${BASE}/review/items/${globalId}`);
    await page.getByRole('button', { name: '返回上一页' }).click();
    await expect(page).toHaveURL(/kind=summary/);
    await expect(page.locator('#lib-kind')).toHaveValue('summary');
    await shot(page, 'review-hub');

    await spa(page, `/courses/${LA}/review`);
    await expect(page.getByTestId('course-review-list').locator(`li[data-document-id="${globalId}"]`)).toBeVisible();
    const courseIds = await page.getByTestId('course-review-list').locator('li[data-document-id]').evaluateAll(items => items.map(item => item.getAttribute('data-document-id')));
    await expect(page.getByTestId('course-review-list').locator(`section[data-class-id="${ENDED}"]`)).toBeVisible();
    await spa(page, `/classrooms/${ENDED}/review`);
    await expect(page.getByTestId('class-review-list').locator(`li[data-document-id="${globalId}"]`)).toBeVisible();
    const classIds = await page.getByTestId('class-review-list').locator('li').evaluateAll(items => items.map(item => item.getAttribute('data-document-id')));
    expect(courseIds).toContain(globalId);
    expect(classIds).toContain(globalId);
    await page.getByTestId('class-review-list').locator(`li[data-document-id="${globalId}"]`).getByRole('link', { name: '打开' }).click();
    await expect(page).toHaveURL(`${BASE}/review/items/${globalId}`);
  });

  test('the reader opens the adopted version; sources are frozen; a changed source is never swapped (UI-REVIEW-04/07)', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(versionRow(page, ADOPTED)).toHaveAttribute('aria-current', 'true');
    await expect(page.getByTestId('source-status')).toContainText('仍按生成时的来源');
    await page.getByTestId('toggle-sources').click();
    await expect(page.getByTestId('audio-trace')).toContainText('开发预览不提供播放');
    await expect(page.getByTestId('source-item').filter({ hasText: '今天讨论特征值' })).toHaveAttribute('data-source-state', 'current');
    await expect(page.getByTestId('source-item').filter({ hasText: '板书' })).toHaveCount(1);
    await versionRow(page, CANDIDATE).getByRole('button', { name: /^查看/ }).click();
    await expect(page.getByTestId('candidate-warning')).toBeVisible();
    await expect(page.locator('[data-testid="source-item"][data-source-state="changed"]')).toContainText('不会用新版本代替');
    await expect(page.getByTestId('document-state')).toContainText('已有采用版本');
    // Font size is an account preference (CAS) and the reading region keeps its own scroll.
    await page.getByRole('button', { name: '放大字号' }).click();
    await expect(page.getByTestId('reading-font')).toHaveText('18px');
    await expect(page.getByTestId('reader')).toHaveCSS('font-size', '18px');
    await shot(page, 'reader-sources');
  });

  test('comparing and adopting are explicit; the previous adoption stays available to switch back (UI-REVIEW-08/09)', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await page.getByRole('button', { name: '比较版本' }).click();
    await page.getByLabel('旧版本').selectOption(ADOPTED);
    await page.getByLabel('新版本').selectOption(CANDIDATE);
    await expect(page.getByTestId('compare')).toContainText('+ - 几何意义');
    await expect(page.getByTestId('compare')).toContainText('两个版本基于不同的来源快照生成');
    await page.getByTestId(`adopt-${CANDIDATE}`).click();
    await expect(page.getByTestId('review-notice')).toContainText('已采用所选版本');
    await expect(versionRow(page, CANDIDATE)).toHaveAttribute('data-role', 'adopted');
    await expect(versionRow(page, ADOPTED)).toHaveAttribute('data-role', 'candidate');
    await page.getByTestId(`adopt-${ADOPTED}`).click();
    await expect(versionRow(page, ADOPTED)).toHaveAttribute('data-role', 'adopted');
    await shot(page, 'reader-compare');
  });

  test('a stale adoption or understood toggle is refused and nothing is changed (CAS)', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(page.getByTestId('document-state')).toContainText('尚未理解');
    expect(await writeElsewhere(page, `/review-documents/${SUMMARY}`, 'PATCH', { base_revision: 1, title: '另一台设备改了标题' })).toBe(200);
    await page.getByTestId(`adopt-${CANDIDATE}`).click();
    await expect(page.getByTestId('review-notice')).toContainText('没有改动');
    await expect(versionRow(page, ADOPTED)).toHaveAttribute('data-role', 'adopted');
    await expect(page.getByRole('heading', { name: '另一台设备改了标题', level: 1 })).toBeVisible();
    // After the refresh the same choice succeeds on the current revision with a new key.
    await page.getByTestId(`adopt-${CANDIDATE}`).click();
    await expect(versionRow(page, CANDIDATE)).toHaveAttribute('data-role', 'adopted');
    expect(await writeElsewhere(page, `/review-documents/${SUMMARY}`, 'PATCH', { base_revision: 3, understood: true })).toBe(200);
    await page.getByTestId('toggle-understood').click();
    await expect(page.getByTestId('review-notice')).toContainText('理解状态未改动');
    await expect(page.getByTestId('document-state')).toContainText('已理解');
  });

  test('a personal draft survives navigation; saving creates a new personal revision and changes nothing else', async ({ page }) => {
    await ready(page, `/review/items/${NOTE}`);
    await page.getByTestId('edit-personal').click();
    await page.locator('#personal-body').fill('## 我的补充\n草稿内容（合成）');
    await spa(page, '/review');
    await expect(page.getByRole('heading', { name: '复习', level: 1 })).toBeVisible();
    await spa(page, `/review/items/${NOTE}`);
    await page.getByTestId('edit-personal').click();
    await expect(page.locator('#personal-body')).toHaveValue('## 我的补充\n草稿内容（合成）');
    await page.getByRole('button', { name: '保存为个人稿' }).click();
    await expect(page.getByTestId('review-notice')).toContainText('个人稿已保存为新版本');
    await expect(page.getByTestId('version-list').locator('li[data-role="personal_latest"]')).toHaveCount(1);
    await expect(page.getByTestId('version-list').locator('li[data-role="personal_earlier"]')).toHaveCount(1);
    await expect(page.getByTestId('reader')).toContainText('草稿内容（合成）');
  });

  test('cards: one at a time, reveal, previous/next, never padded; revealing never marks understood (UI-REVIEW-05/06)', async ({ page }) => {
    await ready(page, `/review/items/${CARDS}`);
    const deck = page.getByTestId('card-deck');
    await expect(deck).toContainText('第 1 / 3 张');
    await expect(page.getByTestId('card-back')).toHaveCount(0);
    await deck.getByRole('button', { name: '揭晓答案' }).click();
    await expect(page.getByTestId('card-back')).toContainText('Av = λv');
    await deck.getByRole('button', { name: '下一张' }).click();
    await expect(deck).toContainText('第 2 / 3 张');
    await expect(page.getByTestId('card-back')).toHaveCount(0);
    await deck.getByRole('button', { name: '下一张' }).click();
    await expect(deck.getByRole('button', { name: '下一张' })).toBeDisabled();
    await expect(deck).toContainText('缺失：课堂未讨论复数特征值的情形');
    await deck.getByRole('button', { name: '标记没懂' }).click();
    await expect(deck.getByRole('button', { name: '已标记为没懂' })).toBeDisabled();
    await expect(page.getByTestId('document-state')).toContainText('尚未理解');
    await shot(page, 'reader-cards');
  });

  test('retranscription: frozen plan, explicit confirmation, unadopted candidate compared by time, explicit adoption and switch back', async ({ page }) => {
    await ready(page, `/classrooms/${ENDED}/review`);
    await page.getByRole('button', { name: '准备重新转写' }).click();
    const plan = page.getByTestId('retranscribe-plan');
    await expect(plan).toContainText('已冻结');
    await expect(plan).toContainText('最高费用');
    await expect(page.getByTestId('retranscribe-tasks')).toHaveCount(0);
    await page.getByTestId('retranscribe-confirm').click();
    await expect(page.getByTestId('retranscribe-tasks').locator('li')).toHaveAttribute('data-state', 'queued');
    await page.getByRole('button', { name: '任务 → 完成' }).click();
    await expect(page.getByTestId('retranscribe-tasks').locator('li')).toHaveAttribute('data-state', 'succeeded');
    const list = page.getByTestId('transcript-version-list');
    await expect(list.locator('li[data-adopted="true"]')).toContainText('实时转写');
    await expect(list).toContainText('候选（未采用）');
    await expect(page.getByTestId('transcript-compare')).toContainText('合成·重新转写');
    await shot(page, 'retranscribe-compare');
    await page.locator('[data-testid^="adopt-transcript-"]').click();
    await expect(page.getByTestId('transcript-notice')).toContainText('已采用所选转写版本');
    await expect(list.locator('li[data-adopted="true"]')).toContainText('重新转写');
    await list.getByRole('button', { name: '切回此版本' }).click();
    await expect(list.locator('li[data-adopted="true"]')).toContainText('实时转写');
  });

  test('the four refusals are shown by error code; a session-quota refusal stays pending approval', async ({ page }) => {
    await ready(page, `/classrooms/${ENDED}/review`);
    const devAudio = page.locator('[aria-label="重新转写模拟（PR-10b）"]');
    const refusal = page.getByTestId('retranscribe-refusal');
    await devAudio.getByRole('button', { name: '原音到期' }).click();
    await page.getByRole('button', { name: '准备重新转写' }).click();
    await expect(refusal).toHaveAttribute('data-code', 'AUDIO_EXPIRED');
    await devAudio.getByRole('button', { name: '原音撤权' }).click();
    await page.getByRole('button', { name: '准备重新转写' }).click();
    await expect(refusal).toHaveAttribute('data-code', 'AUDIO_DELETED');
    await devAudio.getByRole('button', { name: '原音正常' }).click();
    await devAudio.getByRole('button', { name: '本课需审批' }).click();
    await page.getByRole('button', { name: '准备重新转写' }).click();
    await page.getByTestId('retranscribe-confirm').click();
    await expect(refusal).toHaveAttribute('data-code', 'QUOTA_SESSION_EXCEEDED');
    await expect(refusal).toContainText('待审批');
    await devAudio.getByRole('button', { name: '本月额度不足' }).click();
    await page.getByRole('button', { name: '准备重新转写' }).click();
    await page.getByTestId('retranscribe-confirm').click();
    await expect(refusal).toHaveAttribute('data-code', 'QUOTA_MONTH_EXCEEDED');
    await expect(page.getByTestId('retranscribe-tasks')).toHaveCount(0);
    await shot(page, 'retranscribe-refusal');
  });

  test('an unknown result can only be reconciled; a retry needs duplicate-billing consent and keeps the original attempt', async ({ page }) => {
    await ready(page, `/classrooms/${ENDED}/review`);
    await page.getByRole('button', { name: '准备重新转写' }).click();
    await page.getByTestId('retranscribe-confirm').click();
    await page.getByRole('button', { name: '任务 → 结果未知' }).click();
    const original = page.getByTestId('retranscribe-tasks').locator('li[data-state="unknown"]');
    await expect(original).toContainText('不会自动重试');
    await expect(original.getByRole('button', { name: '重试' })).toBeDisabled();
    await original.getByRole('button', { name: '核对状态' }).click();
    await expect(original).toContainText('没有重新提交');
    await original.getByLabel(/可能重复计费/).check();
    await original.getByRole('button', { name: '重试' }).click();
    await expect(page.getByTestId('retranscribe-tasks').locator('li[data-state="queued"]')).toContainText('重试');
    await expect(original).toHaveAttribute('data-state', 'unknown');
    await expect(original.getByRole('button', { name: '重试' })).toHaveCount(0);
  });

  test('offline keeps loaded documents readable and disables every write', async ({ page }) => {
    await ready(page, `/review/items/${SUMMARY}`);
    await expect(versionRow(page, ADOPTED)).toBeVisible();
    await page.getByLabel('模拟离线').check();
    await expect(page.getByTestId('offline-banner')).toContainText('当前离线');
    await expect(page.getByTestId('toggle-understood')).toBeDisabled();
    await expect(page.getByTestId(`adopt-${CANDIDATE}`)).toBeDisabled();
    await expect(page.getByTestId('reader')).toContainText('特征值');
  });

  for (const theme of ['day', 'night'] as const) {
    test(`review pages reflow at 320 px with 200% text in ${theme}`, async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem('aurora-theme', value), theme);
      await page.setViewportSize({ width: 320, height: 800 });
      for (const path of ['/review', `/review/items/${SUMMARY}`, `/review/items/${CARDS}`, `/classrooms/${ENDED}/review`]) {
        await ready(page, path);
        await expect(page.locator('main')).toBeVisible();
        if (path === `/review/items/${SUMMARY}`) await page.getByTestId('toggle-sources').click();
        await noOverflow(page);
        await doubleText(page);
        await noOverflow(page);
        await shot(page, `${theme}-320-200pct-${path.replaceAll('/', '_').replace(/^_/, '')}`);
      }
    });
  }
});
