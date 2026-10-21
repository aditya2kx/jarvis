import type { ActionAck } from "@/lib/actions/types";
import type { Grant } from "./policy";

export type AccessOp =
  | { op: "save"; email: string; grant: Grant; note?: string }
  | { op: "revoke"; email: string; note?: string }
  | { op: "view-as"; email: string | null };

/** Client side of POST /api/access; a non-JSON body (e.g. the preview refusal) becomes the error. */
export async function postAccess(body: AccessOp): Promise<ActionAck> {
  const res = await fetch("/api/access", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  try {
    return JSON.parse(text) as ActionAck;
  } catch {
    return { ok: false, error: text || `Request failed (${res.status}). Nothing was saved.` };
  }
}
