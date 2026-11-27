import type { SearchHitRef } from '../revocation';

/**
 * Private feedback context (a request ID and a text excerpt) captured by a page that links to
 * `/feedback`. It stays in this tab's memory under the account epoch that captured it, bound to the
 * content it was taken from (`source`); navigation carries only an opaque reference, so router history
 * (Back after an account switch) never holds the excerpt, and another epoch's lookup finds nothing.
 * Account teardown forgets every entry; a revoked source discards its entries (see `FeedbackPage`).
 * Each entry is stamped with the revocation ledger's search epoch: once held search snippets can no
 * longer be judged one by one (a cascade or ambiguous deletion, a replaced data set), an excerpt taken
 * before that is not trusted either, whether or not the feedback page was open meanwhile.
 */
export type FeedbackContext = { requestId: string | null; excerpt: string | null; source: SearchHitRef | null };

const entries = new Map<string, { accountKey: string; epoch: number; context: FeedbackContext }>();

/** Keeps the context for this account epoch; returns the reference to pass as navigation state. */
export function stashFeedbackContext(accountKey: string, context: FeedbackContext, searchEpoch = 0): string {
  for (const [id, entry] of entries) if (entry.accountKey !== accountKey) entries.delete(id);
  const id = crypto.randomUUID();
  entries.set(id, { accountKey, epoch: searchEpoch, context });
  return id;
}

/** The excerpt behind a reference was taken in an earlier search epoch, so it must be discarded. */
export function feedbackContextStale(reference: unknown, searchEpoch: number): boolean {
  const entry = typeof reference === 'string' ? entries.get(reference) : undefined;
  return !!entry && entry.context.excerpt !== null && entry.epoch !== searchEpoch;
}

/** The context behind a reference, only for the account epoch that stashed it. */
export function readFeedbackContext(accountKey: string, reference: unknown): FeedbackContext | null {
  if (typeof reference !== 'string') return null;
  const entry = entries.get(reference);
  return entry && entry.accountKey === accountKey ? entry.context : null;
}

/** Forgets one reference (its source was revoked). */
export function discardFeedbackContext(reference: unknown) { if (typeof reference === 'string') entries.delete(reference); }

export function forgetFeedbackContexts() { entries.clear(); }
