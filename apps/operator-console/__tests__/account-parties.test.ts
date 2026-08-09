import { describe, expect, it } from "vitest";
import {
  extractCounterpartyMask,
  resolveFromTo,
} from "@/lib/plaid/account-parties";

describe("extractCounterpartyMask", () => {
  it("parses Chase card ending", () => {
    expect(extractCounterpartyMask("Payment to Chase card ending in 6029 05/21")).toBe("6029");
  });

  it("parses ########8208 style", () => {
    expect(
      extractCounterpartyMask(
        "Online Transfer from Bank of America, National A ########8208 transaction",
      ),
    ).toBe("8208");
  });
});

describe("resolveFromTo", () => {
  it("outflow: from = our account, to = card in memo", () => {
    const r = resolveFromTo({
      amount: 100,
      our_mask: "8933",
      our_label: "PLAT BUS CHECKING",
      name: "Payment to Chase card ending in 6029",
    });
    expect(r.from.mask).toBe("8933");
    expect(r.to.mask).toBe("6029");
  });

  it("inflow: to = our account, from = BOA mask", () => {
    const r = resolveFromTo({
      amount: -20000,
      our_mask: "8933",
      our_label: "Bank",
      name: "Online Transfer from Bank of America ########8208",
      counterparty_name: "Bank of America",
    });
    expect(r.to.mask).toBe("8933");
    expect(r.from.mask).toBe("8208");
    expect(r.from.label).toContain("Bank of America");
  });
});
