import { ADP_RUN_TENANT_UUID } from "@/lib/config/stores";

/** Copy + visibility for /payroll ADP Preview vs completed payroll. */

export type AdpPayrollLinkKind = "preview" | "completed" | "missing";

/** Historic Payroll Details report (spike 2026-08-24: #xfm-Payroll Detail). */
export function adpPayrollDetailsUrl(
  tenantUuid: string = ADP_RUN_TENANT_UUID,
): string {
  return `https://runpayrollmain.adp.com/@${tenantUuid}/v2/#xfm-Payroll%20Detail`;
}

export function adpPayrollLinkCopy(opts: {
  unpaid: boolean;
  hasPreview: boolean;
}): {
  kind: AdpPayrollLinkKind;
  linkText: string;
  emptyText: string;
  badge: string;
} {
  if (opts.unpaid) {
    return {
      kind: opts.hasPreview ? "preview" : "missing",
      linkText: "",
      emptyText: "No ADP Preview yet",
      badge: "Preview done",
    };
  }
  return {
    kind: "completed",
    linkText: "Open ADP payroll",
    emptyText: "No ADP payroll link",
    badge: "Completed",
  };
}

/**
 * Open biweek: hide ADP chrome.
 * Closed unpaid: Run XOR Preview-done (no URL — session hashes 404).
 * Paid/historic: Payroll Details link only.
 */
export function adpPayrollChrome(opts: {
  isCurrent: boolean;
  unpaid: boolean;
  hasPreview: boolean;
  running?: boolean;
}): {
  show: boolean;
  showButton: boolean;
  showLink: boolean;
  kind: AdpPayrollLinkKind;
} {
  const copy = adpPayrollLinkCopy({
    unpaid: opts.unpaid,
    hasPreview: opts.hasPreview,
  });
  if (opts.isCurrent) {
    return { show: false, showButton: false, showLink: false, kind: "missing" };
  }
  if (opts.unpaid) {
    if (opts.running) {
      return { show: true, showButton: true, showLink: false, kind: copy.kind };
    }
    if (opts.hasPreview) {
      return { show: true, showButton: false, showLink: false, kind: "preview" };
    }
    return { show: true, showButton: true, showLink: false, kind: "missing" };
  }
  return { show: true, showButton: false, showLink: true, kind: "completed" };
}
