import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  checkWeeks,
  clampLeadWeeks,
  composeReminder,
  REMINDER_DEFAULT_FOLLOWUP,
  REMINDER_DEFAULT_LEAD_WEEKS,
  REMINDER_DEFAULT_TEMPLATE,
  unmarkedPeople,
} from "@/lib/automations/unavailabilityReminder";

const golden = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../core/testdata/unavailability_reminder_golden.json"), "utf8"),
);

describe("composeReminder golden fixture (shared with the Python job)", () => {
  for (const c of golden.cases) {
    it(c.name, () => {
      expect(
        composeReminder(
          c.today,
          c.days,
          c.template ?? REMINDER_DEFAULT_TEMPLATE,
          c.followup ?? REMINDER_DEFAULT_FOLLOWUP,
          c.lead_weeks ?? REMINDER_DEFAULT_LEAD_WEEKS,
          c.flagged ?? [],
        ),
      ).toEqual({ kind: c.kind, content: c.content });
    });
  }
});

describe("unmarkedPeople golden fixture (shared with the Python job)", () => {
  for (const c of golden.unmarked) {
    it(c.name, () => {
      const weeks = checkWeeks(c.today, c.lead_weeks);
      expect(weeks).toEqual(c.check_weeks);
      expect(unmarkedPeople(c.roster, c.rows, weeks)).toEqual(c.flagged);
    });
  }
});

describe("clampLeadWeeks", () => {
  it("defaults to 4 and stays within 2–8", () => {
    expect([clampLeadWeeks(null), clampLeadWeeks(1), clampLeadWeeks(20), clampLeadWeeks(3)]).toEqual([4, 2, 8, 3]);
  });
});
