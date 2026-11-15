import { beforeAll, describe, expect, it } from "vitest";
import { POST as act } from "@/app/api/rooms/[id]/action/route";
import { POST as claim } from "@/app/api/rooms/[id]/claim/route";
import { POST as join } from "@/app/api/rooms/[id]/join/route";
import { GET as poll } from "@/app/api/rooms/[id]/route";
import { POST as create } from "@/app/api/rooms/route";

/** Calls the real route handlers against the app's in-process Postgres (no DATABASE_URL in tests). */
type Res = Record<string, unknown> & { status: number; room?: { hostId: string; players: { id: string; name: string }[]; hand: { street: string; toAct: string } | null } };

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

beforeAll(async () => {
  const c = await call(create as never, "/api/rooms", { name: "Harry", startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical" });
  rid = c.roomId as string;
  host = c.token as string;
  const j = await call(join as never, `/api/rooms/${rid}/join`, { name: "Ron" }, undefined, rid);
  ron = j.token as string;
  ronId = j.you as string;
  hostId = j.room!.hostId;
  expect((await call(act as never, "/action", { kind: "start" }, host, rid)).status).toBe(200);
}, 30_000);

describe("routes enforce who may do what", () => {
  it("a phone that already holds a seat can't request another one", async () => {
    const r = await call(claim as never, "/claim", { playerId: hostId }, ron, rid);
    expect(r).toMatchObject({ status: 400, error: "You already have a seat at this table" });
  });

  it("a stranger who sits down can't approve their own request for the host's seat", async () => {
    const mallory = (await call(join as never, "/join", { name: "Mallory" }, undefined, rid)).token as string;
    const req = await call(claim as never, "/claim", { playerId: hostId }, undefined, rid);
    const r = await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, mallory, rid);
    expect(r).toMatchObject({ status: 400, error: "Only someone who was already playing before this request can let them in" });
    expect((await call(poll as never, "", undefined, req.token as string, rid)).you).toBeNull();

    const ok = await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: false }, ron, rid);
    expect(ok.status).toBe(200);
  });

  it("someone dealt in before the request can let the real owner back in", async () => {
    const req = await call(claim as never, "/claim", { playerId: hostId }, undefined, rid);
    expect((await call(act as never, "/action", { kind: "seat", claimId: req.claimId, allow: true }, ron, rid)).status).toBe(200);
    expect((await call(poll as never, "", undefined, req.token as string, rid)).you).toBe(hostId);
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
