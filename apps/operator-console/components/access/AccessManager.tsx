"use client";

import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { EyeIcon, SearchIcon, ShieldAlertIcon, UserPlusIcon } from "lucide-react";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PersonAccessDrawer } from "./PersonAccessDrawer";
import { PageChips, PersonAvatar, RoleBadge, relativeTime } from "./visuals";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { formatDateTime } from "@/lib/format";
import type { AccessPerson, PersonRole } from "@/lib/access/people";
import type { GrantVersion } from "@/lib/access/policy";
import { cn } from "@/lib/utils";

type Filter = "all" | Exclude<PersonRole, "owner">;

const FILTERS: { id: Filter; label: string; tone?: string }[] = [
  { id: "all", label: "Everyone" },
  { id: "full", label: "Everything", tone: "bg-emerald-500" },
  { id: "custom", label: "Custom", tone: "bg-sky-500" },
  { id: "default", label: "Unreviewed", tone: "bg-amber-500" },
  { id: "removed", label: "No access", tone: "bg-muted-foreground/40" },
];

export function AccessManager({ people, history }: { people: AccessPerson[]; history: GrantVersion[] }) {
  const [editing, setEditing] = useState<AccessPerson | null>(null);
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const { isPending, run } = useConsoleAction();

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: people.length, full: 0, custom: 0, default: 0, removed: 0 };
    for (const p of people) if (p.role !== "owner") c[p.role] += 1;
    return c;
  }, [people]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return people.filter(
      (p) =>
        (filter === "all" || p.role === filter) &&
        (!q || p.email.includes(q) || (p.note ?? "").toLowerCase().includes(q)),
    );
  }, [people, filter, query]);

  function edit(p: AccessPerson | null) {
    setEditing(p);
    setOpened((n) => n + 1);
    setOpen(true);
  }

  async function viewAs(email: string) {
    const ack = await run(() => postAccess({ op: "view-as", email }), { saving: "Switching view…" });
    // Full load: the root layout (nav, View as strip) only re-renders on a document request.
    if (ack.ok) window.location.assign("/");
  }

  const columns: ColumnDef<AccessPerson>[] = [
    {
      accessorKey: "email",
      header: "Person",
      cell: ({ row }) => {
        const p = row.original;
        return (
          <button
            type="button"
            onClick={() => (p.role === "owner" ? undefined : edit(p))}
            disabled={p.role === "owner"}
            className="group flex min-w-0 items-center gap-3 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
          >
            <PersonAvatar email={p.email} muted={p.role === "removed"} />
            <span className="flex min-w-0 flex-col">
              <span
                className={cn(
                  "truncate font-medium",
                  p.role !== "owner" && "group-hover:underline group-hover:underline-offset-2",
                  p.role === "removed" && "text-muted-foreground",
                )}
              >
                {p.email}
              </span>
              {p.note ? <span className="truncate text-xs text-muted-foreground">{p.note}</span> : null}
            </span>
          </button>
        );
      },
    },
    {
      accessorKey: "role",
      header: "Access",
      cell: ({ row }) => <RoleBadge role={row.original.role} />,
    },
    {
      accessorKey: "summary",
      header: "Can see",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.role === "owner" ? (
          <span className="text-sm text-muted-foreground">Everything · manages access</span>
        ) : row.original.role === "removed" ? (
          <span className="text-sm text-muted-foreground">Nothing</span>
        ) : (
          <PageChips grant={row.original.grant} className="max-w-md" />
        ),
    },
    {
      accessorKey: "signIn",
      header: "Sign-in",
      cell: ({ row }) => {
        const s = row.original.signIn;
        if (s === null) return <span className="text-muted-foreground">—</span>;
        return (
          <span className={cn("inline-flex items-center gap-1.5 text-sm", !s && "text-muted-foreground")}>
            <span aria-hidden className={cn("size-1.5 rounded-full", s ? "bg-emerald-500" : "bg-muted-foreground/40")} />
            {s ? "Allowed" : "Blocked"}
          </span>
        );
      },
    },
    {
      accessorKey: "changedAt",
      header: "Last change",
      cell: ({ row }) =>
        row.original.changedAt ? (
          <div className="flex flex-col text-xs text-muted-foreground" title={formatDateTime(row.original.changedAt)}>
            <span className="tabular-nums text-foreground" suppressHydrationWarning>
              {relativeTime(row.original.changedAt)}
            </span>
            <span className="truncate">by {row.original.changedBy?.split("@")[0]}</span>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      id: "actions",
      header: "",
      enableSorting: false,
      cell: ({ row }) =>
        row.original.role === "owner" ? null : (
          <div className="flex justify-end gap-1">
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    className="max-md:size-10"
                    disabled={isPending}
                    onClick={() => viewAs(row.original.email)}
                    aria-label={`View the console as ${row.original.email}`}
                  >
                    <EyeIcon />
                  </Button>
                }
              />
              <TooltipContent>View as {row.original.email.split("@")[0]}</TooltipContent>
            </Tooltip>
            <Button size="sm" variant="outline" className="max-md:h-10" onClick={() => edit(row.original)}>
              Edit
            </Button>
          </div>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div
        className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none] sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0 lg:grid-cols-5"
        role="tablist"
        aria-label="Filter people"
      >
        {FILTERS.map((f) => {
          const on = filter === f.id;
          return (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setFilter(on && f.id !== "all" ? "all" : f.id)}
              data-testid={`access-filter-${f.id}`}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-full border bg-card px-3.5 py-2 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-col sm:items-start sm:gap-0.5 sm:rounded-lg sm:px-3 sm:py-2.5",
                on ? "border-primary/60 ring-1 ring-primary/30" : "hover:border-foreground/20",
              )}
            >
              <span className="flex items-center gap-1.5 text-sm whitespace-nowrap text-muted-foreground sm:text-xs">
                {f.tone ? <span aria-hidden className={cn("size-1.5 rounded-full", f.tone)} /> : null}
                {f.label}
              </span>
              <span className="text-sm font-semibold tabular-nums sm:text-lg sm:tracking-tight">{counts[f.id]}</span>
            </button>
          );
        })}
      </div>

      {counts.default > 0 && filter !== "default" ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-4 py-3">
          <ShieldAlertIcon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
          <p className="min-w-0 flex-1 text-sm">
            <span className="font-medium">
              {counts.default} {counts.default === 1 ? "person still sees" : "people still see"} everything
            </span>
            <span className="text-muted-foreground"> — they signed in before access was managed here.</span>
          </p>
          <Button size="sm" variant="outline" className="max-md:h-10" onClick={() => setFilter("default")}>
            Review
          </Button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            placeholder="Search people"
            aria-label="Search people"
            className="h-9 pl-8 max-md:h-10"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button onClick={() => edit(null)} className="ml-auto min-h-10 sm:min-h-9" data-testid="add-person">
          <UserPlusIcon data-icon="inline-start" />
          Add person
        </Button>
      </div>

      {rows.length ? (
        <DataTable columns={columns} data={rows} getRowId={(p) => p.email} />
      ) : (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-10 text-center">
          <p className="text-sm font-medium">No one matches</p>
          <p className="text-sm text-muted-foreground">Try another filter or search, or add someone.</p>
        </div>
      )}
      <PersonAccessDrawer key={opened} open={open} onOpenChange={setOpen} person={editing} history={history} />
    </div>
  );
}
