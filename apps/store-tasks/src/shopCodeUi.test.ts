import { describe, expect, it } from "vitest";
import { shopCodeButtonLabel, shopCodeStatus } from "./shopCodeUi";

describe("shopCodeStatus", () => {
  it("prefers shown digits over needs_rotate", () => {
    expect(shopCodeStatus({ set: true, code: "123456", needs_rotate: false })).toBe("shown");
    expect(shopCodeStatus({ set: true, code: null, needs_rotate: true })).toBe("needs_rotate");
    expect(shopCodeStatus({ set: false, code: null, needs_rotate: false })).toBe("unset");
  });
});

describe("shopCodeButtonLabel", () => {
  it("covers unset / set × blank / custom / busy", () => {
    const unset = { set: false, code: null, needs_rotate: false };
    const set = { set: true, code: "111222", needs_rotate: false };
    expect(shopCodeButtonLabel(unset, 0, false)).toBe("Issue code");
    expect(shopCodeButtonLabel(unset, 6, false)).toBe("Set code");
    expect(shopCodeButtonLabel(set, 0, false)).toBe("Issue new code");
    expect(shopCodeButtonLabel(set, 6, false)).toBe("Change code");
    expect(shopCodeButtonLabel(set, 0, true)).toBe("Saving…");
  });
});
