import { describe, expect, it } from "vitest";
import { adpPayrollChrome, adpPayrollDetailsUrl, adpPayrollLinkCopy } from "./adpLink";

describe("adpPayrollLinkCopy", () => {
  it("unpaid with a preview is Preview done, no href copy", () => {
    const c = adpPayrollLinkCopy({ unpaid: true, hasPreview: true });
    expect(c.kind).toBe("preview");
    expect(c.linkText).toBe("");
    expect(c.badge).toBe("Preview done");
  });

  it("paid is completed payroll", () => {
    const c = adpPayrollLinkCopy({ unpaid: false, hasPreview: false });
    expect(c.kind).toBe("completed");
    expect(c.linkText).toBe("Open ADP payroll");
  });
});

describe("adpPayrollChrome", () => {
  it("hides chrome on the open in-progress biweek", () => {
    expect(
      adpPayrollChrome({
        isCurrent: true,
        unpaid: true,
        hasPreview: false,
      }),
    ).toMatchObject({ show: false, showButton: false, showLink: false });
  });

  it("closed unpaid without a preview shows the run button only", () => {
    expect(
      adpPayrollChrome({
        isCurrent: false,
        unpaid: true,
        hasPreview: false,
      }),
    ).toMatchObject({ show: true, showButton: true, showLink: false });
  });

  it("closed unpaid after preview shows status, not a link or button", () => {
    expect(
      adpPayrollChrome({
        isCurrent: false,
        unpaid: true,
        hasPreview: true,
      }),
    ).toMatchObject({
      show: true,
      showButton: false,
      showLink: false,
      kind: "preview",
    });
  });

  it("processing keeps the button and never a preview href", () => {
    expect(
      adpPayrollChrome({
        isCurrent: false,
        unpaid: true,
        hasPreview: true,
        running: true,
      }),
    ).toMatchObject({ show: true, showButton: true, showLink: false });
  });

  it("submitted unpaid is completed chrome, not preview", () => {
    const c = adpPayrollLinkCopy({
      unpaid: true,
      hasPreview: true,
      submitted: true,
    });
    expect(c.kind).toBe("completed");
    expect(c.badge).toBe("Submitted");
    expect(
      adpPayrollChrome({
        isCurrent: false,
        unpaid: true,
        hasPreview: true,
        submitted: true,
      }),
    ).toMatchObject({
      show: true,
      showButton: false,
      showLink: true,
      kind: "completed",
    });
  });

  it("paid historic shows only the completed payroll link", () => {
    expect(
      adpPayrollChrome({
        isCurrent: false,
        unpaid: false,
        hasPreview: false,
      }),
    ).toMatchObject({
      show: true,
      showButton: false,
      showLink: true,
      kind: "completed",
    });
  });

  it("builds the Payroll Details deep link from the store tenant", () => {
    expect(adpPayrollDetailsUrl("836d254c-789b-41b8-8052-d48a639e95d8")).toBe(
      "https://runpayrollmain.adp.com/@836d254c-789b-41b8-8052-d48a639e95d8/v2/#xfm-Payroll%20Detail",
    );
  });
});
