import { describe, expect, it } from "vitest";
import type { Day, Task } from "./api";
import { applyPending, enqueue, type Pending } from "./queue";

const tick = (id: string, kind: Pending["kind"] = "complete"): Pending => ({ id, kind, at: "2026-11-19T07:00:00-06:00", token: "t", staff_id: "s1" });
const task = (id: string, status: Task["status"] = "open") => ({ id, status, completed_by: null, completed_at: null }) as Task;

describe("offline tick queue", () => {
  it("keeps one entry per task and cancels a tick with its undo", () => {
    let q = enqueue([], tick("a"));
    q = enqueue(q, tick("a"));
    expect(q).toHaveLength(1);
    q = enqueue(q, tick("b"));
    q = enqueue(q, tick("a", "reopen"));
    expect(q.map((p) => p.id)).toEqual(["b"]);
  });

  it("shows unsent ticks on top of the server's day", () => {
    const day = { tasks: [task("a"), task("b", "done")] } as Day;
    const shown = applyPending(day, [tick("a"), tick("b", "reopen")]);
    expect(shown.tasks.map((t) => [t.status, t.completed_by, t.pending])).toEqual([
      ["done", "s1", true],
      ["open", null, true],
    ]);
    expect(applyPending(day, [])).toBe(day);
  });
});
