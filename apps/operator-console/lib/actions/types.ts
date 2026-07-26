/** Shared server-action ack — every mutating control returns this shape. */
export type ActionAck<T = unknown> =
  | {
      ok: true;
      message?: string;
      /** Human labels for durable async follow-ups, e.g. "order-reco", "model-recompute". */
      queued?: string[];
      data?: T;
    }
  | { ok: false; error: string };

export function okAck<T = unknown>(opts?: {
  message?: string;
  queued?: string[];
  data?: T;
}): ActionAck<T> {
  return { ok: true, message: opts?.message, queued: opts?.queued, data: opts?.data };
}

export function failAck(error: unknown): ActionAck<never> {
  return {
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  };
}

/** Catch throws from legacy bodies into ActionAck. */
export async function asAck<T>(fn: () => Promise<T>, message?: string): Promise<ActionAck<T>> {
  try {
    const data = await fn();
    return okAck({ data, message });
  } catch (e) {
    return failAck(e);
  }
}
