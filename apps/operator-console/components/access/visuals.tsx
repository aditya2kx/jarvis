import { KeyRoundIcon, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { NAV_GROUPS } from "@/components/shell/nav-items";
import { ACCESS_PAGES, type PageId } from "@/lib/access/registry";
import { canPage, sectionsGranted, type Grant } from "@/lib/access/policy";
import type { PersonRole } from "@/lib/access/people";
import { cn } from "@/lib/utils";

const NAV_ICON = new Map(NAV_GROUPS.flatMap((g) => g.items).map((i) => [i.href, i.icon]));

const PAGE_ICON: Record<string, LucideIcon> = Object.fromEntries(
  ACCESS_PAGES.map((p) => [p.id, NAV_ICON.get(p.href) ?? KeyRoundIcon]),
);

/** The page's sidebar icon, so access UI and navigation read as the same thing. */
export function PageIcon({ page, className }: { page: PageId; className?: string }) {
  const Icon = PAGE_ICON[page];
  return <Icon className={className} aria-hidden />;
}

const ROLE: Record<PersonRole, { label: string; dot: string; className: string }> = {
  owner: {
    label: "Owner",
    dot: "bg-violet-500",
    className: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  },
  full: {
    label: "Everything",
    dot: "bg-emerald-500",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  custom: {
    label: "Custom",
    dot: "bg-sky-500",
    className: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
  default: {
    label: "Unreviewed",
    dot: "bg-amber-500",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  },
  removed: { label: "No access", dot: "bg-muted-foreground/40", className: "text-muted-foreground" },
};

export function roleLabel(role: PersonRole): string {
  return ROLE[role].label;
}

export function RoleBadge({ role }: { role: PersonRole }) {
  return (
    <Badge variant="outline" className={ROLE[role].className}>
      <span aria-hidden className={cn("size-1.5 rounded-full", ROLE[role].dot)} />
      {ROLE[role].label}
    </Badge>
  );
}

const AVATAR_TONES = [
  "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  "bg-teal-500/15 text-teal-700 dark:text-teal-300",
];

function initials(email: string): string {
  const name = email.split("@")[0].replace(/[^a-z]+/gi, " ").trim();
  const parts = name.split(" ").filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (name.slice(0, 2) || "?").toUpperCase();
}

/** Initials on a stable per-email tint, so the same person looks the same everywhere. */
export function PersonAvatar({ email, size = "md", muted }: { email: string; size?: "sm" | "md" | "lg"; muted?: boolean }) {
  const hash = [...email].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold tracking-tight ring-1 ring-inset ring-border/60",
        size === "sm" && "size-7 text-[0.65rem]",
        size === "md" && "size-9 text-xs",
        size === "lg" && "size-11 text-sm",
        muted ? "bg-muted text-muted-foreground" : AVATAR_TONES[hash % AVATAR_TONES.length],
      )}
    >
      {initials(email)}
    </span>
  );
}

/** What someone can see, as one chip per page; a partly granted page shows "3/10". */
export function PageChips({ grant, max = 4, className }: { grant: Grant; max?: number; className?: string }) {
  if (grant.full) {
    return <span className={cn("text-sm text-muted-foreground", className)}>Every page and section</span>;
  }
  const pages = ACCESS_PAGES.filter((p) => canPage(grant, p.id));
  if (!pages.length) return <span className={cn("text-sm text-muted-foreground", className)}>Nothing</span>;
  const shown = pages.slice(0, max);
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((p) => {
        const n = sectionsGranted(grant, p.id);
        const partial = n < p.sections.length;
        return (
          <span
            key={p.id}
            className="inline-flex h-6 items-center gap-1 rounded-md border bg-muted/40 px-1.5 text-xs text-foreground"
            title={partial ? `${p.label}: ${n} of ${p.sections.length} sections` : `${p.label}: whole page`}
          >
            <PageIcon page={p.id} className="size-3 text-muted-foreground" />
            {p.label}
            {partial ? <span className="tabular-nums text-muted-foreground">{n}/{p.sections.length}</span> : null}
          </span>
        );
      })}
      {pages.length > max ? (
        <span className="text-xs text-muted-foreground" title={pages.slice(max).map((p) => p.label).join(", ")}>
          +{pages.length - max} more
        </span>
      ) : null}
    </div>
  );
}

/** "3 min ago" / "Yesterday" / "Oct 4" — short, for lists. */
export function relativeTime(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "Just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 172800) return "Yesterday";
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} days ago`;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "America/Chicago" });
}
