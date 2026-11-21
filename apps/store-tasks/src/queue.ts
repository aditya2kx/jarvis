import { api, isOffline, SHOP, type Day, type Task } from "./api";

/** A change made on this device that the server has not confirmed yet. */
export interface Pending {
  id: string;
  kind: "complete" | "reopen" | "assign";
  at: string;
  token: string;
  staff_id: string;
  /** For "assign": who is doing the task, or null for no one. */
  assignee_id?: string | null;
}

const KEY = "st.queue";

export const loadQueue = (): Pending[] => JSON.parse(localStorage.getItem(KEY) ?? "[]");
const saveQueue = (q: Pending[]) => localStorage.setItem(KEY, JSON.stringify(q));

/**
 * Add a change. A tick and its undo for the same task cancel out, and only the latest
 * assignment of a task is kept, so only real changes are sent.
 */
export function enqueue(queue: Pending[], p: Pending): Pending[] {
  if (p.kind === "assign") return [...queue.filter((q) => !(q.kind === "assign" && q.id === p.id)), p];
  const last = [...queue].reverse().find((q) => q.id === p.id && q.kind !== "assign");
  if (last && last.kind !== p.kind) return queue.filter((q) => q !== last);
  if (last) return queue;
  return [...queue, p];
}

function apply(t: Task, p: Pending): Task {
  switch (p.kind) {
    case "complete":
      // As the API: the shop device ticks for the assignee.
      const by = p.staff_id === SHOP ? (t.assignee_id ?? SHOP) : p.staff_id;
      return { ...t, status: "done", completed_by: by, ticked_by: p.staff_id, completed_at: p.at, pending: true };
    case "reopen":
      return { ...t, status: "open", completed_by: null, ticked_by: null, completed_at: null, pending: true };
    case "assign":
      return { ...t, assignee_id: p.assignee_id ?? null };
    default: {
      const never: never = p.kind;
      throw new Error(`unknown change ${never}`);
    }
  }
}

/** The day as the person sees it: server state with this device's unsent changes applied on top. */
export function applyPending(day: Day, queue: Pending[]): Day {
  if (!queue.length) return day;
  const byId = new Map(day.tasks.map((t) => [t.id, t]));
  for (const p of queue) {
    const t = byId.get(p.id);
    if (t) byId.set(p.id, apply(t, p));
  }
  return { ...day, tasks: day.tasks.map((t) => byId.get(t.id) ?? t) };
}

export function push(...changes: Pending[]): Pending[] {
  const next = changes.reduce(enqueue, loadQueue());
  saveQueue(next);
  return next;
}

const same = (a: Pending, b: Pending) => a.id === b.id && a.at === b.at && a.kind === b.kind;

let flushing = false;

/** Send queued changes in order. Stops at the first network failure; drops what the server refuses. */
export async function flush(): Promise<{ left: number; rejected: string[] }> {
  if (flushing) return { left: loadQueue().length, rejected: [] };
  flushing = true;
  const rejected: string[] = [];
  try {
    for (;;) {
      const queue = loadQueue();
      const [head] = queue;
      if (!head) break;
      // Assigning a whole list is one request, not one per task.
      const batch =
        head.kind === "assign"
          ? queue.filter((q) => q.kind === "assign" && q.token === head.token && (q.assignee_id ?? null) === (head.assignee_id ?? null))
          : [head];
      try {
        if (head.kind === "complete") await api.complete(head.token, head.id, head.at);
        else if (head.kind === "reopen") await api.reopen(head.token, head.id);
        else await api.assign(head.token, batch.map((q) => q.id), head.assignee_id ?? null);
      } catch (e) {
        if (isOffline(e)) break;
        rejected.push((e as Error).message);
      }
      saveQueue(loadQueue().filter((q) => !batch.some((b) => same(b, q))));
    }
  } finally {
    flushing = false;
  }
  return { left: loadQueue().length, rejected };
}
