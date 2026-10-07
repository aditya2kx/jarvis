import "server-only";

import { dateParam, fq, intParam, mutate, q } from "@/lib/bq/client";
import type { ChatRow, ShiftAgg } from "@/lib/recognition/context";
import type { Member } from "@/lib/recognition/parse";

// BQ reads/writes for Monthly recognition (Issue #369, migration 087).

const BATCH = 200;

export type ChatUpsertRow = {
  channel_id: string;
  message_id: string;
  parent_message_id: string; // "" = top-level
  user_id: string;
  posted_at: string; // ISO
  content: string;
  reply_count: number;
};

export async function upsertChatMessages(store: string, rows: ChatUpsertRow[]): Promise<number> {
  for (let i = 0; i < rows.length; i += BATCH) {
    const batch = rows.slice(i, i + BATCH);
    await mutate(
      `MERGE ${fq("clickup_chat_messages")} T
       USING (
         SELECT @store AS store, r.channel_id, r.message_id,
                NULLIF(r.parent_message_id, '') AS parent_message_id,
                NULLIF(r.user_id, '') AS user_id,
                TIMESTAMP(r.posted_at) AS posted_at,
                r.content, CAST(r.reply_count AS INT64) AS reply_count
         FROM UNNEST(@rows) r
       ) S
       ON T.channel_id = S.channel_id AND T.message_id = S.message_id
       WHEN MATCHED THEN UPDATE SET
         parent_message_id = S.parent_message_id, user_id = S.user_id,
         posted_at = S.posted_at, content = S.content,
         reply_count = S.reply_count, synced_at = CURRENT_TIMESTAMP()
       WHEN NOT MATCHED THEN INSERT
         (store, channel_id, message_id, parent_message_id, user_id, posted_at,
          content, reply_count, synced_at)
       VALUES (S.store, S.channel_id, S.message_id, S.parent_message_id, S.user_id,
               S.posted_at, S.content, S.reply_count, CURRENT_TIMESTAMP())`,
      { store, rows: batch },
    );
  }
  return rows.length;
}

export async function upsertMembers(workspaceId: string, members: Member[]): Promise<number> {
  if (!members.length) return 0;
  await mutate(
    `MERGE ${fq("clickup_members")} T
     USING (
       SELECT @ws AS workspace_id, m.userId AS user_id, m.username,
              NULLIF(m.email, '') AS email
       FROM UNNEST(@members) m
     ) S
     ON T.workspace_id = S.workspace_id AND T.user_id = S.user_id
     WHEN MATCHED THEN UPDATE SET
       username = S.username, email = S.email, synced_at = CURRENT_TIMESTAMP()
     WHEN NOT MATCHED THEN INSERT (workspace_id, user_id, username, email, synced_at)
     VALUES (S.workspace_id, S.user_id, S.username, S.email, CURRENT_TIMESTAMP())`,
    {
      ws: workspaceId,
      members: members.map((m) => ({ userId: m.userId, username: m.username, email: m.email ?? "" })),
    },
  );
  return members.length;
}

/** message_id → stored reply_count for a channel's top-level messages. */
export async function storedReplyCounts(channelId: string): Promise<Map<string, number>> {
  const rows = await q<{ message_id: string; reply_count: number | null }>(
    `SELECT message_id, reply_count FROM ${fq("clickup_chat_messages")}
     WHERE channel_id = @ch AND parent_message_id IS NULL`,
    { ch: channelId },
  );
  return new Map(rows.map((r) => [r.message_id, Number(r.reply_count ?? 0)]));
}

export async function hasAnyMessages(channelId: string): Promise<boolean> {
  const rows = await q<{ n: number }>(
    `SELECT COUNT(*) AS n FROM ${fq("clickup_chat_messages")} WHERE channel_id = @ch`,
    { ch: channelId },
  );
  return Number(rows[0]?.n ?? 0) > 0;
}

export type SyncRun = {
  run_id: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "failed";
  messages_upserted: number | null;
  members_upserted: number | null;
  error: string | null;
};

export async function insertSyncRun(
  store: string,
  run: Omit<SyncRun, "finished_at"> & { finished: boolean },
  updatedBy: string,
): Promise<void> {
  await mutate(
    `INSERT INTO ${fq("clickup_sync_runs")}
       (store, run_id, trigger, started_at, finished_at, status,
        messages_upserted, members_upserted, error, updated_by)
     VALUES (@store, @run_id, @trigger, TIMESTAMP(@started_at),
             IF(@finished, CURRENT_TIMESTAMP(), NULL), @status,
             @messages, @members, @error, @by)`,
    {
      store,
      run_id: run.run_id,
      trigger: run.trigger,
      started_at: run.started_at,
      finished: run.finished,
      status: run.status,
      messages: run.messages_upserted,
      members: run.members_upserted,
      error: run.error,
      by: updatedBy,
    },
    { messages: "INT64", members: "INT64", error: "STRING" },
  );
}

/** Latest state per run (append-only log → newest row per run_id), newest run first. */
export async function latestSyncRun(store: string): Promise<SyncRun | null> {
  const rows = await q<SyncRun>(
    `SELECT run_id, trigger, FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', started_at) AS started_at,
            FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', finished_at) AS finished_at, status,
            messages_upserted, members_upserted, error
     FROM ${fq("clickup_sync_runs")}
     WHERE store = @store
     QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY IFNULL(finished_at, started_at) DESC) = 1
     ORDER BY started_at DESC
     LIMIT 1`,
    { store },
  );
  return rows[0] ?? null;
}

export async function chatRows(
  channelIds: string[],
  sinceIso: string,
  untilIso: string,
): Promise<ChatRow[]> {
  return q<ChatRow>(
    `SELECT channel_id, message_id, parent_message_id, user_id,
            FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', posted_at) AS posted_at, content
     FROM ${fq("clickup_chat_messages")}
     WHERE channel_id IN UNNEST(@chs)
       AND posted_at BETWEEN TIMESTAMP(@since) AND TIMESTAMP(@until)
     ORDER BY posted_at`,
    { chs: channelIds, since: sinceIso, until: untilIso },
  );
}

export async function members(workspaceId: string): Promise<Member[]> {
  return q<Member>(
    `SELECT user_id AS userId, IFNULL(username, '') AS username, email
     FROM ${fq("clickup_members")} WHERE workspace_id = @ws`,
    { ws: workspaceId },
  );
}

/** Payroll canonical names active in the last ~6 months. */
export async function rosterNames(): Promise<string[]> {
  const rows = await q<{ name: string }>(
    `SELECT DISTINCT canonical_name AS name FROM ${fq("adp_shifts")}
     WHERE canonical_name IS NOT NULL AND canonical_name != ""
       AND date >= DATE_SUB(CURRENT_DATE("America/Chicago"), INTERVAL 180 DAY)`,
  );
  return rows.map((r) => r.name);
}

/**
 * Per-employee shift aggregates for a date window. Opening = clock-in by 07:30,
 * closing = clock-out at/after 20:30 (adp_shifts in/out are local HH:MM).
 */
export async function shiftAggs(start: string, end: string): Promise<Record<string, ShiftAgg>> {
  const rows = await q<ShiftAgg & { name: string }>(
    `SELECT canonical_name AS name,
            COUNT(DISTINCT date) AS days,
            ROUND(SUM(IFNULL(total_hours, 0)), 1) AS hours,
            COUNTIF(in_time IS NOT NULL AND in_time <= '07:30') AS opening,
            COUNTIF(out_time IS NOT NULL AND out_time >= '20:30') AS closing,
            CAST(MIN(date) AS STRING) AS first_date,
            CAST(MAX(date) AS STRING) AS last_date
     FROM ${fq("adp_shifts")}
     WHERE date BETWEEN @start AND @end AND IFNULL(total_hours, 0) > 0
       AND canonical_name IS NOT NULL
     GROUP BY name`,
    { start: dateParam(start), end: dateParam(end) },
  );
  return Object.fromEntries(
    rows.map((r) => [
      r.name,
      {
        days: Number(r.days),
        hours: Number(r.hours),
        opening: Number(r.opening),
        closing: Number(r.closing),
        first_date: r.first_date,
        last_date: r.last_date,
      },
    ]),
  );
}

// ── Gift-card ledger ──────────────────────────────────────────────────────

/** "external" = operator bought/sent the card outside the console (no Square id). */
export type GiftCardStatus = "pending" | "created" | "activated" | "emailed" | "failed" | "external";

export type GiftCardRow = {
  store: string;
  award_month: string;
  idempotency_key: string;
  clickup_user_id: string;
  award: string;
  recipient_first: string | null;
  recipient_last: string | null;
  recipient_email: string | null;
  card_index: number;
  amount_cents: number;
  location_id: string;
  square_gift_card_id: string | null;
  gan_last4: string | null;
  status: GiftCardStatus;
  email_message_id: string | null;
  error: string | null;
  approved_by: string | null;
  created_at: string;
  updated_at: string;
};

export async function giftCardsForMonth(store: string, awardMonth: string): Promise<GiftCardRow[]> {
  return q<GiftCardRow>(
    `SELECT * EXCEPT (created_at, updated_at),
            CAST(created_at AS STRING) AS created_at, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("recognition_gift_cards")}
     WHERE store = @store AND award_month = @m
     ORDER BY award, recipient_first, card_index`,
    { store, m: awardMonth },
  );
}

export async function giftCardByKey(key: string): Promise<GiftCardRow | null> {
  const rows = await q<GiftCardRow>(
    `SELECT * EXCEPT (created_at, updated_at),
            CAST(created_at AS STRING) AS created_at, CAST(updated_at AS STRING) AS updated_at
     FROM ${fq("recognition_gift_cards")} WHERE idempotency_key = @k`,
    { k: key },
  );
  return rows[0] ?? null;
}

/** Insert the row (pending, or external for cards issued by hand); no-op when the key already exists. */
export async function insertPendingGiftCard(row: {
  store: string;
  award_month: string;
  idempotency_key: string;
  clickup_user_id: string;
  award: string;
  recipient_first: string;
  recipient_last: string;
  recipient_email: string;
  card_index: number;
  amount_cents: number;
  location_id: string;
  approved_by: string;
  status?: "pending" | "external";
}): Promise<void> {
  const { status = "pending", ...fields } = row;
  await mutate(
    `MERGE ${fq("recognition_gift_cards")} T
     USING (SELECT @idempotency_key AS idempotency_key) S
     ON T.idempotency_key = S.idempotency_key
     WHEN NOT MATCHED THEN INSERT
       (store, award_month, idempotency_key, clickup_user_id, award,
        recipient_first, recipient_last, recipient_email, card_index, amount_cents,
        location_id, status, approved_by, created_at, updated_at)
     VALUES (@store, @award_month, @idempotency_key, @clickup_user_id, @award,
             @recipient_first, @recipient_last, @recipient_email, @card_index, @amount_cents,
             @location_id, @status, @approved_by, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())`,
    {
      ...fields,
      status,
      card_index: intParam(row.card_index),
      amount_cents: intParam(row.amount_cents),
    },
  );
}

export async function updateGiftCard(
  key: string,
  patch: {
    status: GiftCardStatus;
    square_gift_card_id?: string | null;
    gan_last4?: string | null;
    email_message_id?: string | null;
    error?: string | null;
  },
): Promise<void> {
  await mutate(
    `UPDATE ${fq("recognition_gift_cards")} SET
       status = @status,
       square_gift_card_id = IFNULL(@gc, square_gift_card_id),
       gan_last4 = IFNULL(@last4, gan_last4),
       email_message_id = IFNULL(@mid, email_message_id),
       error = @error,
       updated_at = CURRENT_TIMESTAMP()
     WHERE idempotency_key = @key`,
    {
      key,
      status: patch.status,
      gc: patch.square_gift_card_id ?? null,
      last4: patch.gan_last4 ?? null,
      mid: patch.email_message_id ?? null,
      error: patch.error ?? null,
    },
    { gc: "STRING", last4: "STRING", mid: "STRING", error: "STRING" },
  );
}
