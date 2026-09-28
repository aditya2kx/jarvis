import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/auth/identity", () => ({
  operatorEmail: async () => "op@test",
  DEFAULT_STORE: "palmetto",
}));
const replaceOrderTubOverrides = vi.fn();
vi.mock("@/lib/bq/writes", () => ({
  replaceOrderTubOverrides: (...a: unknown[]) => replaceOrderTubOverrides(...a),
}));
const requestOrderRecoRefresh = vi.fn();
vi.mock("@/lib/bhaga/orderReco", () => ({
  requestOrderRecoRefresh: (...a: unknown[]) => requestOrderRecoRefresh(...a),
  orderRecoLastChange: vi.fn(),
  orderRecoStatus: vi.fn(),
}));

describe("inventory write → order-reco refresh", () => {
  beforeEach(() => {
    replaceOrderTubOverrides.mockReset().mockResolvedValue(undefined);
    requestOrderRecoRefresh.mockReset();
  });

  it("returns the run id to follow when the refresh starts", async () => {
    requestOrderRecoRefresh.mockResolvedValue({ runId: "console-1" });
    const { applyOrderTubOverridesAction } = await import("@/app/inventory/actions");
    const ack = await applyOrderTubOverridesAction("2026-10-16", [{ item: "Açaí", quantityTubs: 12 }]);
    expect(ack).toMatchObject({ ok: true, queued: ["order-reco"], data: { runId: "console-1" } });
    expect(requestOrderRecoRefresh).toHaveBeenCalledWith(
      expect.objectContaining({ trigger: "order-tub-pins", requestedBy: "op@test" }),
    );
  });

  it("keeps a saved edit successful when the refresh fails to start", async () => {
    requestOrderRecoRefresh.mockRejectedValue(new Error("BigQuery 503"));
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const { applyOrderTubOverridesAction } = await import("@/app/inventory/actions");
    const ack = await applyOrderTubOverridesAction("2026-10-16", [{ item: "Açaí", quantityTubs: 12 }]);
    expect(replaceOrderTubOverrides).toHaveBeenCalledTimes(1);
    expect(ack.ok).toBe(true);
    expect(ack.ok && ack.queued).toBeUndefined();
    expect(ack.ok && ack.message).toMatch(/Update now/);
    expect(String(err.mock.calls[0]?.[0])).toMatch(/^order_reco_submit_failed trigger=order-tub-pins/);
    err.mockRestore();
  });
});
