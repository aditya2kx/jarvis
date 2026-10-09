import { PageHeader } from "@/components/shell/PageHeader";
import { getAutomation, listAutomationPosts } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { chicagoTodayIso } from "@/lib/automations/teamPulse";
import { REMINDER_AUTOMATION_ID } from "@/lib/automations/unavailabilityReminder";
import { ReminderEditor } from "./ReminderEditor";

export const dynamic = "force-dynamic";

export default async function UnavailabilityReminderPage() {
  let error: string | undefined;
  let cfg = null;
  let posts: Awaited<ReturnType<typeof listAutomationPosts>> = [];
  try {
    [cfg, posts] = await Promise.all([
      getAutomation(DEFAULT_STORE, REMINDER_AUTOMATION_ID),
      listAutomationPosts(DEFAULT_STORE, REMINDER_AUTOMATION_ID),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Unavailability reminder"
        subtitle="ClickUp DM drafts asking the team to update ADP unavailability before the Friday publish"
      />
      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable: {error}. If a column is missing, apply migration 088
          (<code className="rounded bg-muted px-1">ensure_schema</code>).
        </p>
      ) : (
        <ReminderEditor initial={cfg} posts={posts} todayIso={chicagoTodayIso()} />
      )}
    </div>
  );
}
