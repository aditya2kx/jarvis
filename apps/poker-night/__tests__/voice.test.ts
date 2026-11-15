import { beforeAll, describe, expect, it } from "vitest";
import { POST as join } from "@/app/api/rooms/[id]/join/route";
import { GET as poll } from "@/app/api/rooms/[id]/route";
import { GET as voiceGet, POST as voicePost } from "@/app/api/rooms/[id]/voice/route";
import { POST as create } from "@/app/api/rooms/route";

/** Real route handlers against the app's in-process Postgres (no DATABASE_URL in tests). */
type Res = Record<string, unknown> & { status: number };

async function call(handler: (req: Request, ctx: never) => Promise<Response>, path: string, id: string, body?: unknown, token?: string) {
  const req = new Request(`http://test${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json", ...(token ? { "x-player-token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const res = await handler(req, { params: Promise.resolve({ id }) } as never);
  return { status: res.status, ...(await res.json()) } as Res;
}

type Signal = { id: string; from: string; body: { type: string; sdp: string; n: string; toN: string } };

let rid = "";
let host = "";
let ron = "";
let hostId = "";
let ronId = "";
const HN = "hostnonce1";
const RN = "ronnonce22";

const vpost = (body: Record<string, unknown>, token?: string) => call(voicePost as never, "/voice", rid, body, token);
const vget = (q: string, token?: string) => call(voiceGet as never, `/voice?${q}`, rid, undefined, token);

beforeAll(async () => {
  const c = await call(create as never, "/api/rooms", "", { name: "Harry", startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical" });
  rid = c.roomId as string;
  host = c.token as string;
  const j = await call(join as never, "/join", rid, { name: "Ron" });
  ron = j.token as string;
  ronId = j.you as string;
  hostId = (j.room as { hostId: string }).hostId;
}, 30_000);

describe("voice chat routes", () => {
  it("someone with only the link (no seat) can't join, listen for signals or send any", async () => {
    expect(await vpost({ action: "join", n: "spectator1" })).toMatchObject({ status: 403 });
    expect(await vget("n=spectator1")).toMatchObject({ status: 403 });
    expect(await vpost({ action: "signal", n: "spectator1", to: hostId, toN: HN, type: "offer", sdp: "v=0" }, "not-a-real-token")).toMatchObject({ status: 403 });
  });

  it("joining shows the seat on the mic in every room poll, even unchanged ones", async () => {
    expect(await vpost({ action: "join", n: HN }, host)).toMatchObject({ status: 200 });
    expect(await vpost({ action: "join", n: RN, muted: true }, ron)).toMatchObject({ status: 200 });
    const full = await call(poll as never, "", rid);
    expect(full.voice).toEqual(expect.arrayContaining([{ id: hostId, muted: false }, { id: ronId, muted: true }]));
    const unchanged = await call(poll as never, `?v=${full.version}`, rid);
    expect(unchanged).toMatchObject({ unchanged: true });
    expect((unchanged.voice as unknown[]).length).toBe(2);
    // The public roster never carries the session nonce that signals are addressed to.
    expect(JSON.stringify(full.voice)).not.toContain(HN);
  });

  it("an offer reaches only the addressed seat, once", async () => {
    expect(await vpost({ action: "signal", n: HN, to: ronId, toN: RN, type: "offer", sdp: "v=0 offer" }, host)).toMatchObject({ status: 200 });
    const inbox = await vget(`n=${RN}&after=0`, ron);
    const signals = inbox.signals as Signal[];
    expect(inbox.joined).toBe(true);
    expect(signals).toHaveLength(1);
    expect(signals[0]).toMatchObject({ from: hostId, body: { type: "offer", sdp: "v=0 offer", n: HN, toN: RN } });
    expect((await vget(`n=${RN}&after=${signals[0].id}`, ron)).signals).toEqual([]);
    expect((await vget(`n=${HN}&after=0`, host)).signals).toEqual([]);
  });

  it("signals must come from your own live session and go to a current one", async () => {
    expect(await vpost({ action: "signal", n: "wrongnonce", to: ronId, toN: RN, type: "offer", sdp: "x" }, host)).toMatchObject({
      status: 400,
      error: "You left voice — tap the mic to rejoin",
    });
    expect(await vpost({ action: "signal", n: HN, to: ronId, toN: "stalenonce", type: "offer", sdp: "x" }, host)).toMatchObject({ status: 400, error: "They left voice" });
    expect(await vpost({ action: "signal", n: HN, to: "nobody", toN: RN, type: "offer", sdp: "x" }, host)).toMatchObject({ status: 400, error: "They left voice" });
    expect(await vpost({ action: "signal", n: HN, to: hostId, toN: HN, type: "offer", sdp: "x" }, host)).toMatchObject({ status: 400 });
    expect(await vpost({ action: "signal", n: HN, to: ronId, toN: RN, type: "candidate", sdp: "x" }, host)).toMatchObject({ status: 400 });
    expect(await vpost({ action: "signal", n: HN, to: ronId, toN: RN, type: "offer", sdp: "x".repeat(9001) }, host)).toMatchObject({ status: 400 });
  });

  it("only the session that joined can mute or leave", async () => {
    expect(await vpost({ action: "mute", n: "othertab01", muted: true }, host)).toMatchObject({ status: 400 });
    expect(await vpost({ action: "mute", n: HN, muted: true }, host)).toMatchObject({ status: 200 });
    expect((await call(poll as never, "", rid)).voice).toContainEqual({ id: hostId, muted: true });
    await vpost({ action: "leave", n: "othertab01" }, host);
    expect((await call(poll as never, "", rid)).voice).toContainEqual({ id: hostId, muted: true });
    await vpost({ action: "leave", n: HN }, host);
    expect((await call(poll as never, "", rid)).voice).toEqual([{ id: ronId, muted: true }]);
    expect((await vget(`n=${HN}`, host)).joined).toBe(false);
  });

  it("a phone that spams offers gets cut off", async () => {
    await vpost({ action: "join", n: HN }, host);
    let last: Res = { status: 0 };
    for (let i = 0; i < 41; i++) last = await vpost({ action: "signal", n: HN, to: ronId, toN: RN, type: "offer", sdp: `v=${i}` }, host);
    expect(last).toMatchObject({ status: 400, error: "Voice is reconnecting too often — give it a moment" });
  });
});
