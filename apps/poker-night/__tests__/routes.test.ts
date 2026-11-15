import { beforeAll, describe, expect, it } from "vitest";
import { POST as act } from "@/app/api/rooms/[id]/action/route";
import { POST as claim } from "@/app/api/rooms/[id]/claim/route";
import { POST as join } from "@/app/api/rooms/[id]/join/route";
import { GET as poll } from "@/app/api/rooms/[id]/route";
import { POST as create } from "@/app/api/rooms/route";
import { sql } from "@/lib/db";

/** Calls the real route handlers against the app's in-process Postgres (no DATABASE_URL in tests). */
type Res = Record<string, unknown> & { status: number; room?: { hostId: string; players: { id: string; name: string }[]; hand: { street: string; toAct: string } | null; hostKey?: string } };

async function call(handler: (req: Request, ctx: never) => Promise<Response>, path: string, body?: unknown, token?: string, id = "") {
  const req = new Request(`http://test${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json", ...(token ? { "x-player-token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const res = await handler(req, { params: Promise.resolve({ id }) } as never);
  return { status: res.status, ...(await res.json()) } as Res;
}

let rid = "";
let host = "";
let ron = "";
let hostId = "";
let ronId = "";
let hostKey = "";

beforeAll(async () => {
  const c = await call(create as never, "/api/rooms", { name: "Harry", startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical" });
  rid = c.roomId as string;
  host = c.token as string;
  hostKey = c.hostKey as string;
  expect(hostKey).toBeTruthy();
  const j = await call(join as never, `/api/rooms/${rid}/join`, { name: "Ron" }, undefined, rid);
  ron = j.token as string;
  ronId = j.you as string;
  hostId = j.room!.hostId;
  expect(j.room!.hostKey).toBeUndefined();
  expect((await call(act as never, "/action", { kind: "start" }, host, rid)).status).toBe(200);
}, 30_000);

describe("routes enforce who may do what", () => {
  it("a phone that already holds a seat can't request another one", async () => {
    const r = await call(claim as never, "/claim", { playerId: hostId }, ron, rid);
    expect(r).toMatchObject({ status: 400, error: "You already have a seat at this table" });
  });

  it("poll responses never include the host key", async () => {
    const v = await call(poll as never, "", undefined, host, rid);
    expect(v.room!.hostKey).toBeUndefined();
  });

  it("lobby dual-joiners can't take the host seat while the host is at the table; a really locked-out host gets vouched back", async () => {
    const c = await call(create as never, "/api/rooms", { name: "Harry", startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical" });
    const id = c.roomId as string;
    const harry = c.token as string;
    const m1 = (await call(join as never, "/join", { name: "M1" }, undefined, id)).token as string;
    const m2 = (await call(join as never, "/join", { name: "M2" }, undefined, id)).token as string;
    expect((await call(act as never, "/action", { kind: "start" }, harry, id)).status).toBe(200);
    const hid = (await call(poll as never, "", undefined, harry, id)).room!.hostId;
    const req = await call(claim as never, "/claim", { playerId: hid }, undefined, id);
    for (const voter of [m1, m2]) {
      expect(await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, voter, id)).toMatchObject({
        status: 400,
        error: "Harry's old phone is still at the table — use that one, or try again in a minute",
      });
    }
    expect((await call(poll as never, "", undefined, req.token as string, id)).you).toBeNull();

    // Harry's old phone stops polling for over a minute: both first-hand players must vouch.
    await sql("update seat_seen set seen_at = now() - interval '2 minutes' where room_id = $1", [id]);
    expect((await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, m1, id)).status).toBe(200);
    expect((await call(poll as never, "", undefined, req.token as string, id)).you).toBeNull();
    expect((await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, m2, id)).status).toBe(200);
    expect((await call(poll as never, "", undefined, req.token as string, id)).you).toBe(hid);
  });

  it("the host can let a friend back in", async () => {
    const req = await call(claim as never, "/claim", { playerId: ronId }, undefined, rid);
    expect((await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, host, rid)).status).toBe(200);
    expect((await call(poll as never, "", undefined, req.token as string, rid)).you).toBe(ronId);
  });

  it("the host key reclaims the host seat without a vouch", async () => {
    const req = await call(claim as never, "/claim", { playerId: hostId, hostKey }, undefined, rid);
    expect(req.status).toBe(200);
    expect(req.you).toBe(hostId);
    expect((req.room as { claims?: unknown[] } | undefined)?.claims ?? []).toEqual([]);
  });

  it("a wrong host key still leaves the request waiting", async () => {
    const c = await call(create as never, "/api/rooms", { name: "Host", startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical" });
    const id = c.roomId as string;
    await call(join as never, "/join", { name: "Friend" }, undefined, id);
    expect((await call(act as never, "/action", { kind: "start" }, c.token as string, id)).status).toBe(200);
    const hid = (await call(poll as never, "", undefined, c.token as string, id)).room!.hostId;
    const req = await call(claim as never, "/claim", { playerId: hid, hostKey: "nope" }, undefined, id);
    expect(req.you).toBeNull();
    expect((await call(poll as never, "", undefined, req.token as string, id)).you).toBeNull();
  });

  it("only the host can pay out a showdown", async () => {
    for (let i = 0; i < 12; i++) {
      const v = await call(poll as never, "", undefined, host, rid);
      const h = v.room!.hand!;
      if (h.street === "showdown") break;
      await call(act as never, "/action", { kind: "act", playerId: h.toAct, move: "call" }, host, rid);
      await call(act as never, "/action", { kind: "act", playerId: h.toAct, move: "check" }, host, rid);
    }
    expect((await call(poll as never, "", undefined, host, rid)).room!.hand!.street).toBe("showdown");
    const steal = await call(act as never, "/action", { kind: "showdown", winners: [[ronId]] }, ron, rid);
    expect(steal).toMatchObject({ status: 400, error: "Only the host can do that" });
    const paid = await call(act as never, "/action", { kind: "showdown", winners: [[hostId]] }, host, rid);
    expect(paid.status).toBe(200);
    expect(paid.room!.hand).toBeNull();
  });
});
