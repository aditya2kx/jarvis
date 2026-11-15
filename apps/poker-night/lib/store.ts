import { createHash, randomBytes } from "node:crypto";
import { sql } from "./db";
import { GameError, liveClaims, type Room, type SeatClaim } from "./game";

type Row = { state: Room; tokens: Record<string, string>; version: number };

const ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789";

export function newId(length: number) {
  const bytes = randomBytes(length);
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

export function newToken() {
  return randomBytes(24).toString("base64url");
}

export function hashToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}

export async function loadRoom(id: string): Promise<Row | null> {
  const rows = await sql<Row>("select state, tokens, version from rooms where id = $1", [id]);
  return rows[0] ?? null;
}

export async function insertRoom(room: Room, tokens: Record<string, string>) {
  await sql("delete from rooms where updated_at < now() - interval '30 days'");
  await sql("insert into rooms (id, state, tokens) values ($1, $2, $3)", [
    room.id,
    JSON.stringify(room),
    JSON.stringify(tokens),
  ]);
}

const CLAIM_PREFIX = "claim:";

export function playerIdFor(row: Row, token: string | null) {
  if (!token) return null;
  const id = row.tokens[hashToken(token)];
  return id && !id.startsWith(CLAIM_PREFIX) ? id : null;
}

/** A seat-request token only becomes a player token once someone at the table approves it. */
export function addClaimToken(row: Row, token: string, claimId: string) {
  const live = new Set(liveClaims(row.state).map((c) => CLAIM_PREFIX + c.id));
  for (const [hash, value] of Object.entries(row.tokens)) {
    if (value.startsWith(CLAIM_PREFIX) && !live.has(value)) delete row.tokens[hash];
  }
  row.tokens[hashToken(token)] = CLAIM_PREFIX + claimId;
}

export function settleClaimToken(row: Row, claim: SeatClaim, allow: boolean) {
  for (const [hash, value] of Object.entries(row.tokens)) {
    if (value !== CLAIM_PREFIX + claim.id) continue;
    if (allow) row.tokens[hash] = claim.playerId;
    else delete row.tokens[hash];
  }
}

/** Read-modify-write with optimistic locking so concurrent taps from different phones never overwrite each other. */
export async function mutateRoom<T>(
  id: string,
  fn: (row: Row) => T,
): Promise<{ result: T; row: Row }> {
  for (let attempt = 0; attempt < 8; attempt++) {
    const row = await loadRoom(id);
    if (!row) throw new GameError("Table not found");
    const result = fn(row);
    const updated = await sql<{ version: number }>(
      "update rooms set state = $1, tokens = $2, version = version + 1, updated_at = now() where id = $3 and version = $4 returning version",
      [JSON.stringify(row.state), JSON.stringify(row.tokens), id, row.version],
    );
    if (updated.length === 1) return { result, row: { ...row, version: updated[0].version } };
  }
  throw new GameError("The table is busy — try again");
}
