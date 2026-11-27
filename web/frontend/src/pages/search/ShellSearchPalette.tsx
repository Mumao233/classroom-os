import { useCallback } from 'react';
import { useNavigate } from 'react-router';
import { useAppData } from '../../features/data';
import { useOperations, useSearchEpoch, useSearchHitRevoked } from '../../features/operations';
import { CommandPalette } from '../../features/search/CommandPalette';
import type { SearchResult } from '../../domain/search';

/** The Ctrl/⌘K palette of a signed-in account: the same guarded search and re-authorized opening as `/search`. */
export function ShellSearchPalette({ open, onClose, onStopCapture }: { open: boolean; onClose: () => void; onStopCapture?: () => void }) {
  const { accountKey, online } = useAppData();
  const operations = useOperations();
  const isRevoked = useSearchHitRevoked();
  const searchEpoch = useSearchEpoch();
  const navigate = useNavigate();
  const openResult = useCallback(async (result: SearchResult, route: string, signal: AbortSignal) => {
    await operations.authorize(result, signal);
    onClose();
    navigate(route);
  }, [operations, onClose, navigate]);
  return <CommandPalette open={open} onClose={onClose} accountKey={accountKey} online={online} search={operations.search}
    openResult={openResult} isRevoked={isRevoked} resetKey={searchEpoch} onStopCapture={onStopCapture} />;
}
