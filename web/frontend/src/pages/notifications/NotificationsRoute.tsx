import { useCallback } from 'react';
import { useAppData } from '../../features/data';
import { useOperations, useRevocationVersion } from '../../features/operations';
import { NotificationCenter } from './NotificationCenter';

/**
 * `/notifications`. `refreshToken` changes when the account's event stream announces
 * notification.created; a new list callback makes the centre reload its first page. A confirmed
 * revocation (e.g. a classroom deletion event) starts a fresh centre: rows and pending reads of the
 * old one are discarded and the authorized list is read again. Marking one as read also refreshes the
 * dashboard's notification card (see `createOperations`).
 */
export function NotificationsRoute({ refreshToken = 0 }: { refreshToken?: number }) {
  const { accountKey } = useAppData();
  const operations = useOperations();
  const revocations = useRevocationVersion();
  const list = useCallback((cursor: string | null, signal: AbortSignal) => {
    void refreshToken;
    return operations.notificationPage(cursor, signal);
  }, [operations, refreshToken]);
  return <NotificationCenter key={revocations} accountKey={accountKey} list={list} markRead={operations.readNotification} />;
}
