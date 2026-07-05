import { pipelineRuns, sourcePulls } from "@/lib/bq/queries";
import { formatDate, dateSortKey } from "@/lib/format";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ColumnDef } from "@tanstack/react-table";
import type { PipelineRunRow, SourcePullRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

function StatusBadge({ status }: { status: string | null }) {
  const variant = status === "success" ? "default" : status ? "destructive" : "secondary";
  return <Badge variant={variant}>{status ?? "unknown"}</Badge>;
}

export default async function PipelinePage() {
  let runs: PipelineRunRow[] = [];
  let pulls: SourcePullRow[] = [];
  let error: string | undefined;
  try {
    [runs, pulls] = await Promise.all([pipelineRuns(), sourcePulls()]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const latest = runs[0];
  const latestPerSource = new Map<string, SourcePullRow>();
  for (const p of pulls) {
    if (!latestPerSource.has(p.source)) latestPerSource.set(p.source, p);
  }
  const dataThrough = pulls.reduce<string | undefined>(
    (max, p) => (!max || dateSortKey(p.run_date) > dateSortKey(max) ? p.run_date : max),
    undefined,
  );

  const runColumns: ColumnDef<PipelineRunRow>[] = [
    { accessorKey: "run_date", header: "Run date", meta: { format: { kind: "date" } } },
    { accessorKey: "status", header: "Status", meta: { format: { kind: "status" } } },
    { accessorKey: "failed_step", header: "Failed step" },
    { accessorKey: "runtime_s", header: "Runtime (s)", meta: { format: { kind: "number" } } },
    { accessorKey: "error", header: "Error" },
  ];

  const sourceColumns: ColumnDef<SourcePullRow>[] = [
    { accessorKey: "source", header: "Source" },
    { accessorKey: "status", header: "Status", meta: { format: { kind: "status" } } },
    { accessorKey: "finished_at_utc", header: "Last pull", meta: { format: { kind: "date" } } },
    { accessorKey: "error", header: "Error" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Pipeline Health"
        subtitle="Nightly refresh status and source freshness"
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Latest run</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center gap-2">
                <StatusBadge status={latest?.status ?? null} />
                <span className="text-sm text-muted-foreground">
                  {latest ? formatDate(latest.run_date) : "no runs recorded"}
                </span>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Sources tracked</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">{latestPerSource.size}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Runs recorded</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">{runs.length}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Data through</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">
                  {dataThrough ? formatDate(dataThrough) : "—"}
                </p>
              </CardContent>
            </Card>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Source freshness</h2>
            <DataTable columns={sourceColumns} data={Array.from(latestPerSource.values())} />
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Run history</h2>
            <DataTable columns={runColumns} data={runs} />
          </div>
        </>
      )}
    </div>
  );
}
