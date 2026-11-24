// @vitest-environment jsdom
import { createElement, useState } from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { CommandPalette } from '../src/features/search/CommandPalette';
import { FeedbackDialog } from '../src/features/feedback/FeedbackDialog';
import { NotificationCenter } from '../src/pages/notifications/NotificationCenter';
import { SearchPage } from '../src/pages/search/SearchPage';
import { searchFixture, seedSearchBackend } from '../src/mock/search-backend';
import type { Notification, NotificationPage } from '../src/domain/notifications';

afterEach(cleanup);
const { a, b } = searchFixture;
const hit = seedSearchBackend().accounts.get(a.owner)!.hits[2].result;

it('opens the palette on its keyword field and restores trigger focus on Escape', async () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return createElement('div', null, createElement('button', { onClick: () => setOpen(true) }, '打开'),
      createElement(CommandPalette, { open, onClose: () => setOpen(false), accountKey: a.owner,
        online: true, search: async () => ({ items: [], next_cursor: null }), openResult: async () => {} }));
  }
  const user = userEvent.setup();
  render(createElement(Harness));
  const trigger = screen.getByRole('button', { name: '打开' });
  await user.click(trigger);
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('searchbox')));
  await user.keyboard('{Escape}');
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

it('drops a denied result and strips visible search text on account or offline transitions', async () => {
  const search = vi.fn(async () => ({ items: [hit], next_cursor: null }));
  const openResult = vi.fn(async () => { throw { code: 'GONE' }; });
  const props = { accountKey: a.owner, online: true, search, openResult };
  const user = userEvent.setup();
  const view = render(createElement(SearchPage, props));
  await user.type(screen.getByRole('searchbox'), '合成{Enter}');
  await user.click(await screen.findByRole('button', { name: hit.title }));
  expect(await screen.findByText('这条结果已无法访问，已从列表移除。')).toBeTruthy();
  expect(screen.queryByText(hit.snippet)).toBeNull();
  await user.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByText(hit.snippet)).toBeTruthy();
  view.rerender(createElement(SearchPage, { ...props, accountKey: b.owner, online: false }));
  expect(screen.getByText(/范围不完整/)).toBeTruthy();
  expect(screen.queryByText(hit.snippet)).toBeNull();
});

it.each([
  { action: '更多结果', error: { kind: 'http', status: 401 }, retained: false },
  { action: '更多结果', error: { kind: 'http', status: 503, code: 'SERVICE_DISABLED' }, retained: false },
  { action: hit.title, error: { kind: 'http', status: 401 }, retained: false },
  { action: hit.title, error: { kind: 'http', status: 503, code: 'SERVICE_DISABLED' }, retained: false },
  { action: '更多结果', error: { kind: 'network' }, retained: true },
  { action: hit.title, error: { kind: 'network' }, retained: true },
])('applies the search failure policy for $action ($error.kind, $error.status)', async ({ action, error, retained }) => {
  const search = vi.fn().mockResolvedValueOnce({ items: [hit], next_cursor: 'cursor' }).mockRejectedValueOnce(error);
  const openResult = vi.fn().mockRejectedValueOnce(error);
  render(createElement(SearchPage, { accountKey: a.owner, online: true, search, openResult }));
  const user = userEvent.setup();
  await user.type(screen.getByRole('searchbox'), '合成{Enter}');
  expect(await screen.findByText(hit.snippet)).toBeTruthy();
  await user.click(screen.getByRole('button', { name: action }));
  expect(await screen.findByText(/请重试/)).toBeTruthy();
  expect(Boolean(screen.queryByText(hit.snippet))).toBe(retained);
  expect(Boolean(screen.queryByRole('button', { name: '更多结果' }))).toBe(retained);
});

it('renders a read capture failure as pending', async () => {
  const item: Notification = { id: a.document, kind: 'capture_failure', severity: 'warning',
    object_id: a.classroom, read_at: null, resolved_at: null, created_at: '2026-11-23T00:00:00Z', message_code: 'CAPTURE_LEASE_LOST' };
  const list = async () => ({ items: [item], next_cursor: null });
  const markRead = vi.fn(async () => ({ ...item, read_at: item.created_at }));
  render(createElement(NotificationCenter, { accountKey: a.owner, list, markRead }));
  await userEvent.click(await screen.findByRole('button', { name: '标为已读' }));
  expect(await screen.findByText('已读')).toBeTruthy();
  expect(screen.getByText('待处理')).toBeTruthy();
  expect(screen.queryByText('已解决')).toBeNull();
});

it.each(['read', 'gone'] as const)(
  'drops a stale refresh but reissues it after a newer mark-read outcome (%s)', async outcome => {
    const item: Notification = { id: a.document, kind: 'capture_failure', severity: 'warning',
      object_id: a.classroom, read_at: null, resolved_at: null, created_at: '2026-11-23T00:00:00Z', message_code: 'CAPTURE_LEASE_LOST' };
    const arrived: Notification = { id: b.document, kind: 'task_completed', severity: 'info', object_id: null,
      read_at: null, resolved_at: null, created_at: '2026-11-23T00:00:01Z', message_code: 'TASK_COMPLETED' };
    const markRead = outcome === 'read'
      ? vi.fn(async () => ({ ...item, read_at: item.created_at }))
      : vi.fn(async () => { throw { status: 404 }; });
    let completeStale!: (page: NotificationPage) => void;
    const stale = new Promise<NotificationPage>(resolve => { completeStale = resolve; });
    const fresh: NotificationPage = {
      items: outcome === 'read' ? [{ ...item, read_at: item.created_at }, arrived] : [arrived],
      next_cursor: 'fresh-cursor' };
    const refresh = vi.fn().mockReturnValueOnce(stale).mockResolvedValueOnce(fresh);
    const view = render(createElement(NotificationCenter, {
      accountKey: a.owner, list: async () => ({ items: [item], next_cursor: null }), markRead }));
    await screen.findByRole('button', { name: '标为已读' });
    view.rerender(createElement(NotificationCenter, { accountKey: a.owner, list: refresh, markRead }));
    expect(screen.getByRole('list', { name: '通知列表' }).getAttribute('aria-busy')).toBe('true');
    await userEvent.click(screen.getByRole('button', { name: '标为已读' }));
    // The interrupted first-page request is reissued after the write and its result applied.
    expect(await screen.findByText('任务已完成')).toBeTruthy();
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(refresh.mock.calls[1][0]).toBeNull();
    expect(screen.getByRole('button', { name: '更多通知' })).toBeTruthy();
    if (outcome === 'read') expect(screen.getByText('已读')).toBeTruthy();
    else expect(screen.queryByText('采集已中断')).toBeNull();
    // The superseded response arriving late changes nothing.
    await act(async () => { completeStale({ items: [item], next_cursor: 'stale-cursor' }); });
    expect(screen.getByText('任务已完成')).toBeTruthy();
    expect(screen.queryAllByRole('button', { name: '标为已读' }).length).toBe(1);
  },
);

it.each([
  { action: 'refresh', error: { status: 403 } },
  { action: 'page', error: { status: 401 } },
  { action: 'read', error: { status: 503, code: 'SERVICE_DISABLED' } },
])('clears notifications and pagination after an authorization failure during $action', async ({ action, error }) => {
  const item: Notification = { id: a.document, kind: 'capture_failure', severity: 'warning',
    object_id: a.classroom, read_at: null, resolved_at: null, created_at: '2026-11-23T00:00:00Z', message_code: 'CAPTURE_LEASE_LOST' };
  const page = { items: [item], next_cursor: 'private-cursor' };
  const list = vi.fn().mockResolvedValueOnce(page).mockRejectedValueOnce(error);
  const markRead = vi.fn().mockRejectedValueOnce(error);
  const props = { accountKey: a.owner, list, markRead };
  const user = userEvent.setup();
  const view = render(createElement(NotificationCenter, props));
  expect(await screen.findByText('采集已中断')).toBeTruthy();
  if (action === 'refresh') {
    view.rerender(createElement(NotificationCenter, { ...props, list: async () => { throw error; } }));
  } else if (action === 'page') {
    await user.click(screen.getByRole('button', { name: '更多通知' }));
  } else {
    let resolveRefresh!: (value: NotificationPage) => void;
    const refresh = new Promise<NotificationPage>(resolve => { resolveRefresh = resolve; });
    view.rerender(createElement(NotificationCenter, { ...props, list: () => refresh }));
    await user.click(screen.getByRole('button', { name: '标为已读' }));
    expect(await screen.findByText('通知已无法访问，请重新加载。')).toBeTruthy();
    await act(async () => { resolveRefresh(page); });
  }
  expect(await screen.findByText('通知已无法访问，请重新加载。')).toBeTruthy();
  expect(screen.queryByText('采集已中断')).toBeNull();
  expect(screen.queryByRole('button', { name: '更多通知' })).toBeNull();
});

it.each([{ status: 404 }, { status: 410 }, { code: 'NOT_FOUND' }, { code: 'GONE' }])(
  'removes an unavailable notification after retaining the key through a transient retry (%j)', async error => {
    const item: Notification = { id: a.document, kind: 'capture_failure', severity: 'warning',
      object_id: a.classroom, read_at: null, resolved_at: null, created_at: '2026-11-23T00:00:00Z', message_code: 'CAPTURE_LEASE_LOST' };
    const list = async () => ({ items: [item], next_cursor: null });
    const markRead = vi.fn<(_id: string, _key: string, _signal: AbortSignal) => Promise<Notification>>()
      .mockRejectedValueOnce({ status: 503 }).mockRejectedValueOnce(error);
    const user = userEvent.setup();
    render(createElement(NotificationCenter, { accountKey: a.owner, list, markRead }));
    await user.click(await screen.findByRole('button', { name: '标为已读' }));
    expect(await screen.findByText('暂时无法标为已读，请重试。')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '标为已读' }));
    expect(await screen.findByText('这条通知已无法访问，已从列表移除。')).toBeTruthy();
    expect(screen.queryByText('采集已中断')).toBeNull();
    expect(markRead.mock.calls[0][1]).toBe(markRead.mock.calls[1][1]);
  },
);

it('leaves feedback excerpt consent unchecked and excludes the excerpt on submit', async () => {
  const submit = vi.fn(async () => ({ accepted: true, request_id: a.document }));
  const user = userEvent.setup();
  render(createElement(FeedbackDialog, { buildId: 'test', open: true, onClose: () => {},
    accountKey: a.owner, excerpt: 'private fixture', submit }));
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  await user.type(screen.getByRole('textbox', { name: '问题说明' }), 'synthetic');
  await user.click(screen.getByRole('button', { name: '提交反馈' }));
  expect(await screen.findByText('反馈已提交。')).toBeTruthy();
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ confirmed_excerpt: null, confirm_send_excerpt: false }),
    expect.any(String), expect.any(AbortSignal));
});
