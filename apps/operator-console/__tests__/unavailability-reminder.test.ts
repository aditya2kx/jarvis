import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  composeReminder,
  REMINDER_DEFAULT_FOLLOWUP,
  REMINDER_DEFAULT_TEMPLATE,
} from "@/lib/automations/unavailabilityReminder";

const golden = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../core/testdata/unavailability_reminder_golden.json"), "utf8"),
);

describe("composeReminder golden fixture (shared with the Python job)", () => {
  for (const c of golden.cases) {
    it(c.name, () => {
      expect(
        composeReminder(c.today, c.days, REMINDER_DEFAULT_TEMPLATE, REMINDER_DEFAULT_FOLLOWUP),
      ).toEqual({ kind: c.kind, content: c.content });
    });
  }
});
