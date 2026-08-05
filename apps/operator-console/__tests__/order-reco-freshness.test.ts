import { describe, expect, it } from "vitest";
import { orderRecoRefreshedAdvanced } from "@/lib/inventory/orderRecoFreshness";

describe("orderRecoRefreshedAdvanced", () => {
  it("advances when latest is after baseline", () => {
    expect(
      orderRecoRefreshedAdvanced(
        "2026-08-05T13:40:00.000Z",
        "2026-08-05T13:43:19.000Z",
      ),
    ).toBe(true);
  });

  it("does not advance when latest equals baseline", () => {
    expect(
      orderRecoRefreshedAdvanced(
        "2026-08-05T13:40:00.000Z",
        "2026-08-05T13:40:00.000Z",
      ),
    ).toBe(false);
  });

  it("does not advance when latest is missing", () => {
    expect(orderRecoRefreshedAdvanced("2026-08-05T13:40:00.000Z", null)).toBe(false);
  });

  it("advances when baseline is null and latest exists", () => {
    expect(orderRecoRefreshedAdvanced(null, "2026-08-05T13:43:19.000Z")).toBe(true);
  });
});
