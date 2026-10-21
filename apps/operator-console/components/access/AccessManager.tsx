"use client";

import { useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { EyeIcon, PencilIcon, UserPlusIcon } from "lucide-react";
import { DataTable } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PersonAccessDrawer } from "./PersonAccessDrawer";
import { postAccess } from "@/lib/access/client";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { formatDateTime } from "@/lib/format";
import type { AccessPerson, PersonRole } from "@/lib/access/people";
import type { GrantVersion } from "@/lib/access/policy";

const ROLE: Record<PersonRole, { label: string; className: string }> = {
  owner: { label: "Owner", className: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300" },
  full: { label: "Full access", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  custom: { label: "Custom", className: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  default: { label: "Not set", className: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300" },
  removed: { label: "No access", className: "text-muted-foreground" },
};

export function RoleBadge({ role }: { role: PersonRole }) {
  return (
    <Badge variant="outline" className={ROLE[role].className}>
      {ROLE[role].label}
    </Badge>
  );
}

export function AccessManager({ people, history }: { people: AccessPerson[]; history: GrantVersion[] }) {
  const [editing, setEditing] = useState<AccessPerson | null>(null);
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(0);
  const { isPending, run } = useConsoleAction();

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
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-medium">{row.original.email}</span>
          {row.original.note ? (
            <span className="truncate text-xs text-muted-foreground">{row.original.note}</span>
          ) : null}
        </div>
      ),
    },
    {
      accessorKey: "role",
      header: "Role",
      cell: ({ row }) => <RoleBadge role={row.original.role} />,
    },
    {
      accessorKey: "summary",
      header: "Can see",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-md text-sm text-muted-foreground">{row.original.summary}</span>
      ),
    },
    {
      accessorKey: "signIn",
      header: "Google sign-in",
      cell: ({ row }) => {
        const s = row.original.signIn;
        if (s === null) return <span className="text-muted-foreground">—</span>;
        return s ? (
          <span className="text-sm">Allowed</span>
        ) : (
          <span className="text-sm text-muted-foreground">Not allowed</span>
        );
      },
    },
    {
      accessorKey: "changedAt",
      header: "Last change",
      cell: ({ row }) =>
        row.original.changedAt ? (
          <div className="flex flex-col text-xs text-muted-foreground">
            <span className="tabular-nums">{formatDateTime(row.original.changedAt)}</span>
            <span className="truncate">by {row.original.changedBy}</span>
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
          <div className="flex justify-end gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              disabled={isPending}
              onClick={() => viewAs(row.original.email)}
              aria-label={`View the console as ${row.original.email}`}
            >
              <EyeIcon data-icon="inline-start" />
              View as
            </Button>
            <Button size="sm" variant="outline" onClick={() => edit(row.original)}>
              <PencilIcon data-icon="inline-start" />
              Edit
            </Button>
          </div>
        ),
    },
  ];

  return (
    <>
      <div className="flex justify-end">
        <Button onClick={() => edit(null)} className="min-h-11 sm:min-h-8" data-testid="add-person">
          <UserPlusIcon data-icon="inline-start" />
          Add person
        </Button>
      </div>
      <DataTable columns={columns} data={people} getRowId={(p) => p.email} />
      <PersonAccessDrawer key={opened} open={open} onOpenChange={setOpen} person={editing} history={history} />
    </>
  );
}
