import { describe, expect, it } from "vitest";
import {
  FULL_ACCESS,
  NO_ACCESS,
  allowedHrefs,
  canKey,
  canPage,
  canSection,
  grantSummary,
  normalizeGrant,
  parseDefaultPolicy,
  parseGrant,
  parseOwners,
  resolveAccess,
  setPageGrant,
  type GrantVersion,
} from "@/lib/access/policy";
import { ACCESS_PAGES, sectionIds } from "@/lib/access/registry";
import { NAV_GROUPS, visibleNavGroups } from "@/components/shell/nav-items";
import { allowedTabs } from "@/components/shell/recentTabs";

const version = (over: Partial<GrantVersion>): GrantVersion => ({
  email: "lindsay@example.com",
  version: 1,
  status: "active",
  grant: NO_ACCESS,
  note: null,
  createdBy: "adi@mypalmetto.co",
  createdAt: "2026-10-21T12:00:00Z",
  ...over,
});

const owners = parseOwners(undefined);

describe("resolveAccess", () => {
  it("owners always get full access, whatever is stored", () => {
    const a = resolveAccess({ email: " ADI@mypalmetto.co ", owners, latest: version({ status: "revoked" }), defaultPolicy: "none" });
    expect(a).toMatchObject({ email: "adi@mypalmetto.co", isOwner: true, source: "owner", grant: FULL_ACCESS });
  });

  it("the newest saved grant wins over the default", () => {
    const grant = normalizeGrant({ pages: { labor: ["coverage"] } });
    const a = resolveAccess({ email: "am@example.com", owners, latest: version({ grant }), defaultPolicy: "full" });
    expect(a.source).toBe("grant");
    expect(a.grant).toEqual(grant);
  });

  it("revoked means no access even when the default is full", () => {
    const a = resolveAccess({ email: "lindsay@example.com", owners, latest: version({ status: "revoked" }), defaultPolicy: "full" });
    expect(a).toMatchObject({ source: "revoked", grant: NO_ACCESS });
  });

  it("people with nothing saved follow CONSOLE_ACCESS_DEFAULT (full keeps today's behavior)", () => {
    expect(resolveAccess({ email: "x@example.com", owners, latest: undefined, defaultPolicy: "full" }).grant).toEqual(FULL_ACCESS);
    expect(resolveAccess({ email: "x@example.com", owners, latest: undefined, defaultPolicy: "none" }).grant).toEqual(NO_ACCESS);
    expect(parseDefaultPolicy(undefined)).toBe("full");
    expect(parseDefaultPolicy(" NONE ")).toBe("none");
  });

  it("CONSOLE_OWNER_EMAILS overrides the default owners", () => {
    expect(parseOwners("A@x.co, b@y.co")).toEqual(["a@x.co", "b@y.co"]);
    expect(parseOwners(" ")).toEqual(["adi@mypalmetto.co", "aditya.2ky@gmail.com"]);
  });
});

describe("page / section checks", () => {
  const grant = normalizeGrant({ pages: { labor: ["coverage", "punch-gaps"], inventory: "all" } });

  it("a section grant opens the page but only those sections", () => {
    expect(canPage(grant, "labor")).toBe(true);
    expect(canSection(grant, "labor", "coverage")).toBe(true);
    expect(canSection(grant, "labor", "labor-wages")).toBe(false);
    expect(canPage(grant, "payroll")).toBe(false);
  });

  it("a whole-page grant includes every section", () => {
    for (const s of sectionIds("inventory")) expect(canSection(grant, "inventory", s)).toBe(true);
  });

  it("canKey reads action access keys", () => {
    expect(canKey(grant, "labor")).toBe(true);
    expect(canKey(grant, "labor:coverage")).toBe(true);
    expect(canKey(grant, "labor:availability")).toBe(false);
    expect(canKey(grant, "payroll")).toBe(false);
    expect(canKey(grant, "nope")).toBe(false);
    expect(canKey(FULL_ACCESS, "payroll:adp-draft")).toBe(true);
  });

  it("allowedHrefs lists granted pages in nav order", () => {
    expect(allowedHrefs(grant)).toEqual(["/labor", "/inventory"]);
    expect(allowedHrefs(NO_ACCESS)).toEqual([]);
  });
});

describe("normalizeGrant / setPageGrant", () => {
  it("drops unknown pages and sections, and empty pages", () => {
    expect(normalizeGrant({ pages: { labor: ["coverage", "bogus"], nope: "all", sales: [] } })).toEqual({
      full: false,
      pages: { labor: ["coverage"] },
    });
    expect(parseGrant("not json")).toEqual(NO_ACCESS);
    expect(normalizeGrant(null)).toEqual(NO_ACCESS);
  });

  it("ticking every section collapses to the whole page", () => {
    expect(normalizeGrant({ pages: { sales: sectionIds("sales") } }).pages.sales).toBe("all");
  });

  it("setPageGrant edits one page and leaves the others", () => {
    const g = normalizeGrant({ pages: { sales: "all" } });
    expect(setPageGrant(g, "labor", ["coverage"]).pages).toEqual({ sales: "all", labor: ["coverage"] });
    expect(setPageGrant(g, "sales", null).pages).toEqual({});
  });

  it("narrowing a full-access person keeps every other page", () => {
    const g = setPageGrant(FULL_ACCESS, "payroll", null);
    expect(g.full).toBe(false);
    expect(allowedHrefs(g)).toEqual(ACCESS_PAGES.filter((p) => p.id !== "payroll").map((p) => p.href));
  });

  it("summarizes for the people table", () => {
    expect(grantSummary(FULL_ACCESS)).toBe("Full access");
    expect(grantSummary(NO_ACCESS)).toBe("No access");
    expect(grantSummary(normalizeGrant({ pages: { sales: "all", labor: ["coverage"] } }))).toBe(
      "Sales · Labor (1 of 10)",
    );
  });
});

describe("navigation follows access", () => {
  it("every access page has a nav entry with the same href", () => {
    const hrefs = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.href));
    for (const p of ACCESS_PAGES) expect(hrefs).toContain(p.href);
  });

  it("hides pages the person can't open and drops empty groups", () => {
    const groups = visibleNavGroups(["/labor", "/inventory"]);
    expect(groups.map((g) => g.label)).toEqual(["Performance", "Inventory"]);
    expect(groups.flatMap((g) => g.items.map((i) => i.href))).toEqual(["/labor", "/inventory"]);
    expect(visibleNavGroups(undefined)).toBe(NAV_GROUPS);
  });

  it("phone tabs keep only allowed pages and fill from the rest of the allowed list", () => {
    const items = NAV_GROUPS.flatMap((g) => g.items);
    const tabs = allowedTabs(
      items.filter((i) => ["/home", "/sales", "/labor", "/payroll"].includes(i.href)),
      ["/labor", "/inventory"],
    );
    expect(tabs.map((t) => t.href)).toEqual(["/labor", "/inventory"]);
  });
});
