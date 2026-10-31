// api/pendingChats.ts
//
// Questions whose answer has not reached the page yet (operator, 2026-10-31: an answer
// outlives its HTTP request, and is recovered after the page was left). Only the
// request id, the question and its conversation are kept -- never the secret -- in
// localStorage, so a closed tab finds them again. Bounded in count and age.

export interface PendingChat {
  id: string;
  question: string;
  /** The earlier conversation it continued; null for the live one. */
  conversation: string | null;
  /** Date.now() when it was asked. */
  at: number;
}

export const PENDING_CHATS_KEY = "gwen-pending-chats";
export const MAX_PENDING_CHATS = 20;
/** Older than this, a question is forgotten: its answer is in the conversation's history. */
export const PENDING_MAX_AGE_MS = 24 * 60 * 60 * 1000;

function store(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export function loadPending(now: number = Date.now()): PendingChat[] {
  let raw: unknown;
  try {
    raw = JSON.parse(store()?.getItem(PENDING_CHATS_KEY) ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (p): p is PendingChat =>
      !!p &&
      typeof p === "object" &&
      typeof p.id === "string" &&
      typeof p.question === "string" &&
      (p.conversation === null || typeof p.conversation === "string") &&
      typeof p.at === "number" &&
      now - p.at < PENDING_MAX_AGE_MS,
  );
}

function save(list: PendingChat[]): void {
  try {
    store()?.setItem(PENDING_CHATS_KEY, JSON.stringify(list.slice(-MAX_PENDING_CHATS)));
  } catch {
    /* storage full or refused: the answer still lands while the page stays open */
  }
}

export function addPending(item: PendingChat): void {
  save([...loadPending().filter((p) => p.id !== item.id), item]);
}

export function removePending(id: string): void {
  save(loadPending().filter((p) => p.id !== id));
}
