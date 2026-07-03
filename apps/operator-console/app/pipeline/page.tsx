import { pipelineRuns, sourcePulls } from "@/lib/bq/queries";
import { formatDate } from "@/lib/format";
import { DataTable } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ColumnDef } from "@tanstack/react-table";
import type { PipelineRunRow, SourcePullRow } from "@/lib/bq/queries";

export const revalidate = 600;

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
      <h1 className="text-2xl font-semibold tracking-tight">Pipeline Health</h1>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
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
