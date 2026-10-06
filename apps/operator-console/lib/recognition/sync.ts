import "server-only";

import {
  DEFAULT_WORKSPACE_ID,
  listChannelMessagesPage,
  listReplies,
  listWorkspaceMembersWithEmail,
  type ClickUpChatMessage,
} from "@/lib/automations/clickup";
import { RECOGNITION } from "@/lib/config/stores";
import {
  hasAnyMessages,
  insertSyncRun,
  latestSyncRun,
  storedReplyCounts,
  upsertChatMessages,
  upsertMembers,
  type ChatUpsertRow,
  type SyncRun,
} from "@/lib/recognition/store";

// Incremental ClickUp → BQ copy for Monthly recognition (Issue #369).
// #monthly-recognition is small and synced in full; #running and Shift Coverage
// page back LOOKBACK_DAYS (FIRST_SYNC_DAYS on an empty table). Threads are
// re-fetched when their reply count changed or they are recent.

const DAY_MS = 86_400_000;
const LOOKBACK_DAYS = 35;
const FIRST_SYNC_DAYS = 75;
const RECENT_THREAD_DAYS = 14;
const REQUEST_GAP_MS = 250;
const MAX_PAGES = 40;
const RUNNING_STALE_MS = 10 * 60_000;
export const AUTO_SYNC_AFTER_MS = 60 * 60_000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function toRow(channelId: string, m: ClickUpChatMessage, parent: string | null): ChatUpsertRow {
  return {
    channel_id: channelId,
    message_id: String(m.id),
    parent_message_id: parent ?? "",
    user_id: m.user_id != null ? String(m.user_id) : "",
    posted_at: new Date(Number(m.date)).toISOString(),
    content: m.content ?? "",
    reply_count: Number(m.replies_count ?? 0),
  };
}

async function syncChannel(channelId: string, full: boolean): Promise<ChatUpsertRow[]> {
  const firstSync = !(await hasAnyMessages(channelId));
  const cutoff = full
    ? 0
    : Date.now() - (firstSync ? FIRST_SYNC_DAYS : LOOKBACK_DAYS) * DAY_MS;
  const known = await storedReplyCounts(channelId);
  const rows: ChatUpsertRow[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const { messages, nextCursor } = await listChannelMessagesPage(channelId, cursor);
    let reachedCutoff = false;
    for (const m of messages) {
      const at = Number(m.date);
      if (at < cutoff) {
        reachedCutoff = true;
        continue;
      }
      rows.push(toRow(channelId, m, null));
      const replies = Number(m.replies_count ?? 0);
      const recent = Date.now() - at < RECENT_THREAD_DAYS * DAY_MS;
      if (replies > 0 && (known.get(String(m.id)) !== replies || recent)) {
        await sleep(REQUEST_GAP_MS);
        for (const r of await listReplies(String(m.id))) rows.push(toRow(channelId, r, String(m.id)));
      }
    }
    cursor = nextCursor;
    if (reachedCutoff || !cursor || !messages.length) break;
    await sleep(REQUEST_GAP_MS);
  }
  return rows;
}

export type SyncResult =
  | { ok: true; skipped: false; run: SyncRun }
  | { ok: true; skipped: true; reason: string; run: SyncRun | null }
  | { ok: false; error: string; run: SyncRun | null };

export async function syncRecognitionSources(
  store: string,
  trigger: "console" | "auto" | "nightly",
  updatedBy: string,
): Promise<SyncResult> {
  const cfg = RECOGNITION[store];
  if (!cfg) return { ok: false, error: `No recognition config for store ${store}`, run: null };

  const last = await latestSyncRun(store);
  if (
    last?.status === "running" &&
    Date.now() - Date.parse(last.started_at) < RUNNING_STALE_MS
  ) {
    return { ok: true, skipped: true, reason: "A sync is already running", run: last };
  }

  const run: SyncRun = {
    run_id: `sync-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    trigger,
    started_at: new Date().toISOString(),
    finished_at: null,
    status: "running",
    messages_upserted: null,
    members_upserted: null,
    error: null,
  };
  await insertSyncRun(store, { ...run, finished: false }, updatedBy);

  try {
    const memberList = await listWorkspaceMembersWithEmail(DEFAULT_WORKSPACE_ID);
    const membersUpserted = await upsertMembers(DEFAULT_WORKSPACE_ID, memberList);
    let messagesUpserted = 0;
    for (const [channelId, full] of [
      [cfg.recognitionChannelId, true],
      [cfg.runningChannelId, false],
      [cfg.coverageChannelId, false],
    ] as const) {
      const rows = await syncChannel(channelId, full);
      messagesUpserted += await upsertChatMessages(store, rows);
    }
    const done: SyncRun = {
      ...run,
      status: "ok",
      finished_at: new Date().toISOString(),
      messages_upserted: messagesUpserted,
      members_upserted: membersUpserted,
    };
    await insertSyncRun(store, { ...done, finished: true }, updatedBy);
    return { ok: true, skipped: false, run: done };
  } catch (e) {
    const error = (e instanceof Error ? e.message : String(e)).slice(0, 500);
    console.error(`[recognition] BREADCRUMB step=sync run=${run.run_id} err=${error}`);
    const failed: SyncRun = { ...run, status: "failed", finished_at: new Date().toISOString(), error };
    await insertSyncRun(store, { ...failed, finished: true }, updatedBy).catch(() => {});
    return { ok: false, error, run: failed };
  }
}
