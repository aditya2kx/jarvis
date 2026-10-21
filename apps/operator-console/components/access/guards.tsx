import "server-only";
import type { ReactNode } from "react";
import { getAccess } from "@/lib/access/server";
import { allowedHrefs, canPage, canSection } from "@/lib/access/policy";
import { ACCESS_PAGES, accessPage, type PageId, type SectionOf } from "@/lib/access/registry";
import { NoAccess } from "./NoAccess";

/**
 * Call first in a page component: `const denied = await pageDenied("labor"); if (denied) return denied;`
 * so a page the person can't open renders the refusal without loading any of its data.
 */
export async function pageDenied(page: PageId): Promise<ReactNode | null> {
  const a = await getAccess();
  if (!a.error && canPage(a.grant, page)) return null;
  const next = ACCESS_PAGES.find((p) => allowedHrefs(a.grant).includes(p.href)) ?? null;
  return (
    <NoAccess
      pageLabel={accessPage(page)!.label}
      email={a.viewingAs ?? a.realEmail}
      nextHref={next?.href ?? null}
      nextLabel={next?.label ?? null}
      error={a.error}
    />
  );
}

/** Same check for a page whose whole route is one section (e.g. an Automations sub-page). */
export async function sectionPageDenied<P extends PageId>(page: P, section: SectionOf<P>): Promise<ReactNode | null> {
  const a = await getAccess();
  if (!a.error && canSection(a.grant, page, section)) return null;
  const label = accessPage(page)!.sections.find((s) => s.id === section)!.label;
  const next = ACCESS_PAGES.find((p) => allowedHrefs(a.grant).includes(p.href)) ?? null;
  return (
    <NoAccess
      pageLabel={label}
      email={a.viewingAs ?? a.realEmail}
      nextHref={next?.href ?? null}
      nextLabel={next?.label ?? null}
      error={a.error}
    />
  );
}

/** Per-section checks for a page: `const can = await sectionAccess("labor"); can("coverage")`. */
export async function sectionAccess<P extends PageId>(page: P): Promise<(section: SectionOf<P>) => boolean> {
  const a = await getAccess();
  return (section) => !a.error && canSection(a.grant, page, section);
}
