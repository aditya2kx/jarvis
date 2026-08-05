import Link from "next/link";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getAutomation, listAutomationPosts } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import {
  AUTOMATION_ID,
  cadenceSummary,
  parseDays,
} from "@/lib/automations/teamPulse";

export const dynamic = "force-dynamic";

export default async function AutomationsPage() {
  let error: string | undefined;
  let cfg = null;
  let lastPost: string | null = null;
  try {
    cfg = await getAutomation(DEFAULT_STORE, AUTOMATION_ID);
    const posts = await listAutomationPosts(DEFAULT_STORE, AUTOMATION_ID, 1);
    lastPost = posts[0]?.posted_at ?? null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const days = parseDays(cfg?.days_of_week ?? "[1,3,6]");
  const cadence = cadenceSummary(
    days,
    cfg?.hour_local ?? 8,
    cfg?.minute_local ?? 0,
    cfg?.timezone ?? "America/Chicago",
  );
  const enabled = cfg?.enabled === true || (cfg?.enabled as unknown) === "true";
  const dest = cfg?.destination ?? "dm";

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Automations"
        subtitle="Scheduled operator automations — start with Team pulse"
      />

      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable: {error}. Apply migration 054 if tables are missing.
        </p>
      ) : (
        <Link
          href="/automations/team-pulse"
          className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Card className="transition-colors hover:bg-muted/40">
            <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
              <div>
                <CardTitle className="text-base">Team pulse (ClickUp)</CardTitle>
                <p className="mt-1 text-sm text-muted-foreground">
                  Review-bonus leaderboard + motivating closer → ClickUp chat
                </p>
              </div>
              <Badge variant={enabled ? "default" : "secondary"}>
                {enabled ? "Enabled" : "Off"}
              </Badge>
            </CardHeader>
            <CardContent className="flex flex-col gap-1 text-sm text-muted-foreground">
              <p>{cadence}</p>
              <p>
                Destination:{" "}
                <span className="text-foreground">
                  {dest === "channel" ? "Group channel" : "DM (test)"}
                </span>
              </p>
              <p>
                Last post:{" "}
                <span className="text-foreground">{lastPost ?? "—"}</span>
              </p>
            </CardContent>
          </Card>
        </Link>
      )}
    </div>
  );
}
