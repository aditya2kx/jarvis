import { sql } from "./db";
import { GameError } from "./game";

/** A member drops out of voice once its phone stops polling for this long (tab closed, phone locked). */
const MEMBER_TTL = "12 seconds";
const SIGNAL_TTL = "60 seconds";
const SIGNALS_PER_MINUTE = 40;
export const SDP_MAX_LENGTH = 9000;

export type VoiceMember = { id: string; n: string; muted: boolean };
export type VoiceSignal = { id: string; from: string; body: SignalBody };
export type SignalBody = { type: "offer" | "answer"; sdp: string; n: string; toN: string };

export function isNonce(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9]{6,16}$/.test(value);
}

export async function voiceMembers(roomId: string): Promise<VoiceMember[]> {
  return sql<VoiceMember>(
    `select player_id as id, nonce as n, muted from voice_members
     where room_id = $1 and seen_at > now() - interval '${MEMBER_TTL}' order by player_id`,
    [roomId],
  );
}

export async function joinVoice(roomId: string, playerId: string, nonce: string, muted: boolean) {
  await sql("delete from voice_members where seen_at < now() - interval '1 day'");
  await sql("delete from voice_signals where created_at < now() - interval '1 hour'");
  await sql(
    `insert into voice_members (room_id, player_id, nonce, muted) values ($1, $2, $3, $4)
     on conflict (room_id, player_id) do update set nonce = excluded.nonce, muted = excluded.muted, seen_at = now()`,
    [roomId, playerId, nonce, muted],
  );
}

export async function leaveVoice(roomId: string, playerId: string, nonce: string) {
  await sql("delete from voice_members where room_id = $1 and player_id = $2 and nonce = $3", [roomId, playerId, nonce]);
}

/** Only the phone that joined with `nonce` may change or refresh the membership. */
export async function touchVoice(roomId: string, playerId: string, nonce: string, muted?: boolean) {
  const rows = await sql(
    `update voice_members set seen_at = now(), muted = coalesce($4, muted)
     where room_id = $1 and player_id = $2 and nonce = $3 returning player_id`,
    [roomId, playerId, nonce, muted ?? null],
  );
  return rows.length === 1;
}

export async function sendSignal(roomId: string, from: string, to: string, body: SignalBody) {
  await sql(`delete from voice_signals where room_id = $1 and created_at < now() - interval '${SIGNAL_TTL}'`, [roomId]);
  const [{ n }] = await sql<{ n: number }>(
    "select count(*)::int as n from voice_signals where room_id = $1 and from_id = $2 and created_at > now() - interval '1 minute'",
    [roomId, from],
  );
  if (n >= SIGNALS_PER_MINUTE) throw new GameError("Voice is reconnecting too often — give it a moment");
  await sql("insert into voice_signals (room_id, from_id, to_id, body) values ($1, $2, $3, $4)", [roomId, from, to, JSON.stringify(body)]);
}

export async function signalsFor(roomId: string, to: string, after: string): Promise<VoiceSignal[]> {
  return sql<VoiceSignal>(
    `select id::text as id, from_id as "from", body from voice_signals
     where room_id = $1 and to_id = $2 and id > $3::bigint and created_at > now() - interval '${SIGNAL_TTL}'
     order by id limit 50`,
    [roomId, to, after],
  );
}
