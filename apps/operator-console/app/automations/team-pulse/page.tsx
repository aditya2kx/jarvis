import { PageHeader } from "@/components/shell/PageHeader";
import {
  getAutomation,
  listAutomationPosts,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { AUTOMATION_ID } from "@/lib/automations/teamPulse";
import {
  listNamedChannels,
  listWorkspaceMembers,
  type ClickUpNamedOption,
} from "@/lib/automations/clickup";
import { TeamPulseEditor } from "./TeamPulseEditor";

export const dynamic = "force-dynamic";

export default async function TeamPulsePage() {
  let error: string | undefined;
  let cfg = null;
  let posts: Awaited<ReturnType<typeof listAutomationPosts>> = [];
  let channels: ClickUpNamedOption[] = [];
  let members: ClickUpNamedOption[] = [];
  let clickupError: string | undefined;

  try {
    [cfg, posts] = await Promise.all([
      getAutomation(DEFAULT_STORE, AUTOMATION_ID),
      listAutomationPosts(DEFAULT_STORE, AUTOMATION_ID),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  try {
    [channels, members] = await Promise.all([
      listNamedChannels(),
      listWorkspaceMembers(),
    ]);
  } catch (e) {
    clickupError = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Team pulse"
        subtitle="ClickUp motivating leaderboard — schedule, template, and history"
      />
      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable: {error}. If tables are missing, apply migration 054
          (<code className="rounded bg-muted px-1">ensure_schema</code>).
        </p>
      ) : (
        <TeamPulseEditor
          initial={cfg}
          posts={posts}
          channels={channels}
          members={members}
          clickupError={clickupError}
        />
      )}
    </div>
  );
}
