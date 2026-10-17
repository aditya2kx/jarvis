import "server-only";
import { triggerAdpScheduleWrite } from "@/lib/bhaga/recompute";

export type ScheduleWriteJob =
  | { mode: "drafts"; pushId: string }
  | { mode: "publish"; weekStart: string }
  | { mode: "dm"; weekStart: string; to?: "dm" | "channel" };

/** Always a Cloud Run job — the ADP browser never runs on the operator's machine. */
export async function startScheduleWrite(
  store: string,
  job: ScheduleWriteJob,
): Promise<{ executionName?: string }> {
  return triggerAdpScheduleWrite(store, job);
}
