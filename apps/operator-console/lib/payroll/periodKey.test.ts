import { describe, expect, it } from "vitest";
import {
  bonusDiffDollars,
  concatRecognitionReasons,
  estGrossPayDollars,
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

  it("adds perks into est total", () => {
    expect(
      estTotalPayDollars({
        estGrossPay: 1922,
        tipsAllocated: 0,
        reviewBonus: 0,
        recognitionBonus: 0,
        perks: 20,
      }),
    ).toBe(1942);
  });

  it("splits regular vs OT at 1.5x when otRate omitted", () => {
    expect(
      estGrossPayDollars({
        hoursWorked: 41,
        otHours: 1,
        wageRate: 25,
        otRate: null,
      }),
    ).toBe(1037.5);
  });

  it("half-up cents like ADP Preview (not IEEE 47.3×15.25→721.32)", () => {
    expect(
      estGrossPayDollars({
        hoursWorked: 47.3,
        otHours: 0,
        wageRate: 15.25,
        otRate: null,
      }),
    ).toBe(721.33);
    expect(
      estGrossPayDollars({
        hoursWorked: 43.98,
        otHours: 0,
        wageRate: 16.25,
        otRate: null,
      }),
    ).toBe(714.68);
  });

  it("returns null gross when rate is missing", () => {
    expect(
      estGrossPayDollars({
        hoursWorked: 6.55,
        otHours: 0,
        wageRate: null,
        otRate: null,
      }),
    ).toBeNull();
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
