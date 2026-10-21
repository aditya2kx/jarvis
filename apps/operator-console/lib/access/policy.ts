import { ACCESS_PAGES, accessPage, sectionIds, type PageId } from "./registry";

/** `"all"` = the whole page, including sections added later; a list = only those sections. */
export type PageGrant = "all" | string[];

export interface Grant {
  full: boolean;
  pages: Partial<Record<PageId, PageGrant>>;
}

export type GrantStatus = "active" | "revoked";

export interface GrantVersion {
  email: string;
  version: number;
  status: GrantStatus;
  grant: Grant;
  note: string | null;
  createdBy: string;
  createdAt: string;
}

/** Who has no saved grant: `full` keeps today's everyone-sees-everything behavior. */
export type DefaultPolicy = "full" | "none";

export type AccessSource = "owner" | "grant" | "revoked" | "default";

export interface Access {
  email: string;
  isOwner: boolean;
  source: AccessSource;
  grant: Grant;
}

export const NO_ACCESS: Grant = { full: false, pages: {} };
export const FULL_ACCESS: Grant = { full: true, pages: {} };

export const DEFAULT_OWNERS = ["adi@mypalmetto.co", "aditya.2ky@gmail.com"];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function isValidEmail(email: string): boolean {
  return EMAIL_RE.test(normalizeEmail(email));
}

export function parseOwners(raw: string | undefined): string[] {
  const list = (raw ?? "").split(",").map(normalizeEmail).filter(Boolean);
  return list.length ? list : DEFAULT_OWNERS;
}

export function parseDefaultPolicy(raw: string | undefined): DefaultPolicy {
  return raw?.trim().toLowerCase() === "none" ? "none" : "full";
}

/**
 * Drops unknown pages/sections and empty pages; a list naming every section of a page
 * collapses to `"all"` so "tick every box" and "whole page" mean the same thing.
 */
export function normalizeGrant(raw: unknown): Grant {
  if (!raw || typeof raw !== "object") return NO_ACCESS;
  const r = raw as { full?: unknown; pages?: unknown };
  if (r.full === true) return FULL_ACCESS;
  const pages: Grant["pages"] = {};
  const src = r.pages && typeof r.pages === "object" ? (r.pages as Record<string, unknown>) : {};
  for (const page of ACCESS_PAGES) {
    const v = src[page.id];
    if (v === "all") {
      pages[page.id] = "all";
    } else if (Array.isArray(v)) {
      const known = sectionIds(page.id);
      const picked = known.filter((id) => v.includes(id));
      if (picked.length === known.length) pages[page.id] = "all";
      else if (picked.length) pages[page.id] = picked;
    }
  }
  return { full: false, pages };
}

export function parseGrant(json: string | null | undefined): Grant {
  if (!json) return NO_ACCESS;
  try {
    return normalizeGrant(JSON.parse(json));
  } catch {
    return NO_ACCESS;
  }
}

export function resolveAccess({
  email,
  owners,
  latest,
  defaultPolicy,
}: {
  email: string;
  owners: string[];
  latest: GrantVersion | undefined;
  defaultPolicy: DefaultPolicy;
}): Access {
  const e = normalizeEmail(email);
  if (owners.includes(e)) return { email: e, isOwner: true, source: "owner", grant: FULL_ACCESS };
  if (latest?.status === "revoked") return { email: e, isOwner: false, source: "revoked", grant: NO_ACCESS };
  if (latest) return { email: e, isOwner: false, source: "grant", grant: latest.grant };
  return {
    email: e,
    isOwner: false,
    source: "default",
    grant: defaultPolicy === "full" ? FULL_ACCESS : NO_ACCESS,
  };
}

export function canPage(grant: Grant, page: PageId): boolean {
  return grant.full || grant.pages[page] !== undefined;
}

export function canSection(grant: Grant, page: PageId, section: string): boolean {
  if (grant.full) return true;
  const g = grant.pages[page];
  return g === "all" || (Array.isArray(g) && g.includes(section));
}

/** `"labor"` (any section of the page) or `"labor:coverage"` (that section). */
export function canKey(grant: Grant, key: string): boolean {
  const [page, section] = key.split(":");
  if (!accessPage(page)) return false;
  return section ? canSection(grant, page as PageId, section) : canPage(grant, page as PageId);
}

export function allowedHrefs(grant: Grant): string[] {
  return ACCESS_PAGES.filter((p) => canPage(grant, p.id)).map((p) => p.href);
}

export function setPageGrant(grant: Grant, page: PageId, value: PageGrant | null): Grant {
  const base: Grant = grant.full
    ? { full: false, pages: Object.fromEntries(ACCESS_PAGES.map((p) => [p.id, "all"])) }
    : { full: false, pages: { ...grant.pages } };
  if (value === null || (Array.isArray(value) && value.length === 0)) delete base.pages[page];
  else base.pages[page] = value;
  return normalizeGrant(base);
}

export function sectionsGranted(grant: Grant, page: PageId): number {
  if (grant.full || grant.pages[page] === "all") return sectionIds(page).length;
  return grant.pages[page]?.length ?? 0;
}

/** One-line description for tables: "Full access", "No access", "Labor (3 of 10) · Payroll & People". */
export function grantSummary(grant: Grant): string {
  if (grant.full) return "Full access";
  const parts = ACCESS_PAGES.filter((p) => canPage(grant, p.id)).map((p) => {
    const g = grant.pages[p.id];
    return g === "all" ? p.label : `${p.label} (${g!.length} of ${p.sections.length})`;
  });
  return parts.length ? parts.join(" · ") : "No access";
}
