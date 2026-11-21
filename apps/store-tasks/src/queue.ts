import { api, isOffline, type Day, type Task } from "./api";

/** A tick made on this device that the server has not confirmed yet. */
export interface Pending {
  id: string;
  kind: "complete" | "reopen";
  at: string;
  token: string;
  staff_id: string;
}

const KEY = "st.queue";

export const loadQueue = (): Pending[] => JSON.parse(localStorage.getItem(KEY) ?? "[]");
const saveQueue = (q: Pending[]) => localStorage.setItem(KEY, JSON.stringify(q));

/** Add a tick. A tick and its undo for the same task cancel out, so only real changes are sent. */
export function enqueue(queue: Pending[], p: Pending): Pending[] {
  const last = [...queue].reverse().find((q) => q.id === p.id);
  if (last && last.kind !== p.kind) return queue.filter((q) => q !== last);
  if (last) return queue;
  return [...queue, p];
}

/** The day as the person sees it: server state with this device's unsent ticks applied on top. */
export function applyPending(day: Day, queue: Pending[]): Day {
  if (!queue.length) return day;
  const byId = new Map(queue.map((q) => [q.id, q]));
  return {
    ...day,
    tasks: day.tasks.map((t): Task => {
      const p = byId.get(t.id);
      if (!p) return t;
      return p.kind === "complete"
        ? { ...t, status: "done", completed_by: p.staff_id, completed_at: p.at, pending: true }
        : { ...t, status: "open", completed_by: null, completed_at: null, pending: true };
    }),
  };
}

export function push(p: Pending): Pending[] {
  const next = enqueue(loadQueue(), p);
  saveQueue(next);
  return next;
}

let flushing = false;

/** Send queued ticks in order. Stops at the first network failure; drops ticks the server refuses. */
export async function flush(): Promise<{ left: number; rejected: string[] }> {
  if (flushing) return { left: loadQueue().length, rejected: [] };
  flushing = true;
  const rejected: string[] = [];
  try {
    for (;;) {
      const [head] = loadQueue();
      if (!head) break;
      try {
        if (head.kind === "complete") await api.complete(head.token, head.id, head.at);
        else await api.reopen(head.token, head.id);
      } catch (e) {
        if (isOffline(e)) break;
        rejected.push((e as Error).message);
      }
      saveQueue(loadQueue().filter((q) => !(q.id === head.id && q.at === head.at && q.kind === head.kind)));
    }
  } finally {
    flushing = false;
  }
  return { left: loadQueue().length, rejected };
}
