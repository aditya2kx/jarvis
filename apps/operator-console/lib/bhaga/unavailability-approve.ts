import "server-only";
import { unavailabilityStillPending } from "@/lib/bq/queries";
import {
  getCloudRunExecutionStatus,
  triggerAdpUnavailabilityApprove,
  type CloudRunExecutionStatus,
} from "@/lib/bhaga/recompute";

/** Always a Cloud Run job — the ADP browser never runs on the operator's machine. */
export async function startUnavailabilityApprove(
  store: string,
  rowKey: string,
  requestedBy: string,
): Promise<{ executionName: string }> {
  if (!(await unavailabilityStillPending(rowKey))) {
    throw new Error("That request is no longer pending in ADP — run Sync ADP to refresh.");
  }
  return triggerAdpUnavailabilityApprove(store, rowKey, requestedBy);
}

export type UnavailabilityApprovePoll = {
  /** True once the refreshed BQ no longer lists the request as pending. */
  done: boolean;
  execution?: CloudRunExecutionStatus;
};

export async function pollUnavailabilityApprove(opts: {
  rowKey: string;
  executionName?: string | null;
}): Promise<UnavailabilityApprovePoll> {
  const execution = opts.executionName
    ? await getCloudRunExecutionStatus(opts.executionName)
    : undefined;
  const done = !(await unavailabilityStillPending(opts.rowKey));
  return { done, execution };
}
