import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  PunchFixesNotInHoursNotice,
  punchFixesNotInHoursMessage,
} from "@/components/labor/PunchFixesNotInHoursNotice";

const CIMINO = {
  date: "2026-10-04",
  employee: "Denton, Cimino R",
  in_time: "06:30",
  out_time: "13:30",
};
const HILLARY = { date: "2026-10-02", employee: "Huynh, Hillary", in_time: null, out_time: "20:33" };

describe("punch fixes not in hours", () => {
  it("renders nothing when every written fix is in hours", () => {
    expect(punchFixesNotInHoursMessage([])).toBeNull();
    expect(renderToStaticMarkup(<PunchFixesNotInHoursNotice rows={[]} />)).toBe("");
  });

  it("names each lagging fix and points at Sync ADP", () => {
    const msg = punchFixesNotInHoursMessage([CIMINO, HILLARY])!;
    expect(msg.headline).toMatch(/^2 punch fixes saved in ADP are not in hours yet/);
    expect(msg.headline).toContain("Sync ADP");
    expect(msg.items[0]).toContain("Denton, Cimino R");
    expect(msg.items[0]).toContain("6:30 AM – 1:30 PM");
    expect(msg.items[1]).toContain("out 8:33 PM");
  });

  it("uses the singular for one fix", () => {
    const html = renderToStaticMarkup(<PunchFixesNotInHoursNotice rows={[CIMINO]} />);
    expect(html).toContain("1 punch fix saved in ADP is not in hours yet");
    expect(html).toContain('data-testid="punch-fixes-not-in-hours"');
  });
});
