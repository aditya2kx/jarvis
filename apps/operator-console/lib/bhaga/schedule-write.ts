import "server-only";
import { triggerAdpScheduleWrite } from "@/lib/bhaga/recompute";

export type ScheduleWriteJob =
  | { mode: "drafts"; pushId: string }
  | { mode: "publish"; weekStart: string }
  | { mode: "dm"; weekStarts: string[]; to?: "dm" | "channel" }
  | { mode: "assign"; assignId: string }
  | { mode: "apply"; batchId: string };

/** Always a Cloud Run job — the ADP browser never runs on the operator's machine. */
export async function startScheduleWrite(
  store: string,
  job: ScheduleWriteJob,
): Promise<{ executionName?: string }> {
  return triggerAdpScheduleWrite(store, job);
}
