import { describe, expect, it } from "vitest";
import {
  bonusDiffDollars,
  concatRecognitionReasons,
  estTotalPayDollars,
  payPeriodKey,
} from "./periodKey";

describe("payPeriodKey", () => {
  it("joins start..end for recognition_bonuses", () => {
    expect(payPeriodKey("2026-07-13", "2026-07-26")).toBe("2026-07-13..2026-07-26");
  });
});

describe("estTotalPayDollars / bonusDiffDollars (migration 049)", () => {
  it("includes recognition in est total", () => {
    expect(
      estTotalPayDollars({
        estGrossPay: 100,
        tipsAllocated: 20,
        reviewBonus: 10,
        recognitionBonus: 5,
      }),
    ).toBe(135);
  });

  it("bonus_diff = review + recognition - ADP bonus", () => {
    expect(
      bonusDiffDollars({
        reviewBonus: 10,
        recognitionBonus: 5,
        adpBonusPaid: 12,
      }),
    ).toBe(3);
  });

  it("treats null ADP bonus as 0", () => {
    expect(
      bonusDiffDollars({
        reviewBonus: 10,
        recognitionBonus: 5,
        adpBonusPaid: null,
      }),
    ).toBe(15);
  });
});

describe("concatRecognitionReasons", () => {
  it("joins non-empty reasons with '; '", () => {
    expect(concatRecognitionReasons(["Great shift", "", "  Helped close  ", null])).toBe(
      "Great shift; Helped close",
    );
  });
});
