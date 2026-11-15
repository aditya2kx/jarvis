import { afterEach, describe, expect, it, vi } from "vitest";
import { addPlayer, canVouch, CLAIM_TTL_MS, createRoom, liveClaims, redeemWithHostKey, requestSeat, resolveClaim, SEAT_AWAY_MS, startHand, type Room } from "@/lib/game";
import { addClaimToken, hashToken, playerIdFor, settleClaimToken } from "@/lib/store";
import { viewFor } from "@/lib/view";

vi.mock("@/lib/db", () => ({ sql: vi.fn() }));

function row() {
  vi.useFakeTimers();
  const state: Room = createRoom("r", { id: "harry", name: "Harry" }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
  addPlayer(state, { id: "ron", name: "Ron" });
  for (const p of state.players) p.stats.handsPlayed = 3;
  state.trustedIds = ["harry", "ron"];
  state.hostKey = "host-secret";
  state.handCount = 1;
  vi.advanceTimersByTime(60_000);
  return { state, tokens: { [hashToken("harry-old")]: "harry", [hashToken("ron-tok")]: "ron" } as Record<string, string>, version: 1 };
}

function settle(r: ReturnType<typeof row>, by: string, claimId: string, allow: boolean, seenAt: Record<string, number> = {}) {
  const { claim, done } = resolveClaim(r.state, by, claimId, allow, seenAt);
  if (done) settleClaimToken(r, claim, allow);
  return done;
}

afterEach(() => vi.useRealTimers());

describe("getting a lost seat back", () => {
  it("a pending request token is not a seat until the host allows it", () => {
    const r = row();
    requestSeat(r.state, "c1", "ron");
    addClaimToken(r, "new-phone", "c1");
    expect(playerIdFor(r, "new-phone")).toBeNull();
    expect(viewFor(r.state, null).claims).toEqual([expect.objectContaining({ id: "c1", playerId: "ron" })]);
    expect(viewFor(r.state, null).hostKey).toBeUndefined();

    expect(settle(r, "harry", "c1", true)).toBe(true);
    expect(playerIdFor(r, "new-phone")).toBe("ron");
    expect(r.state.players).toHaveLength(2);
    expect(viewFor(r.state, "ron").claims).toEqual([]);
  });

  it("turning a request down drops its token", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "stranger", "c1");
    expect(settle(r, "ron", "c1", false)).toBe(true);
    expect(playerIdFor(r, "stranger")).toBeNull();
    expect(Object.values(r.tokens)).toEqual(["harry", "ron"]);
  });

  it("requests expire, can't be approved late, and their tokens get swept", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "slow", "c1");
    vi.advanceTimersByTime(CLAIM_TTL_MS + 1);
    expect(viewFor(r.state, "ron").claims).toEqual([]);
    expect(() => resolveClaim(r.state, "harry", "c1", true)).toThrow(/expired/);
    requestSeat(r.state, "c2", "ron");
    addClaimToken(r, "fresh", "c2");
    expect(Object.values(r.tokens)).toEqual(["harry", "ron", "claim:c2"]);
  });

  it("rejects unknown seats and caps open requests", () => {
    const r = row();
    expect(() => requestSeat(r.state, "x", "nobody")).toThrow(/Unknown player/);
    for (let i = 0; i < 4; i++) requestSeat(r.state, `c${i}`, "ron");
    expect(() => requestSeat(r.state, "c9", "ron")).toThrow(/Too many/);
  });

  it("anyone who has played can let a player back in once that seat's old phone is gone", () => {
    const r = row();
    addPlayer(r.state, { id: "hermione", name: "Hermione" });
    r.state.players.find((p) => p.id === "hermione")!.stats.handsPlayed = 2;
    requestSeat(r.state, "c1", "ron");
    addClaimToken(r, "ron-new", "c1");
    expect(canVouch(r.state, "hermione", r.state.claims![0])).toBe(true);
    const ronAway = { ron: Date.now() - SEAT_AWAY_MS - 1 };
    expect(settle(r, "hermione", "c1", true, ronAway)).toBe(true);
    expect(playerIdFor(r, "ron-new")).toBe("ron");
  });

  it("nobody but the host can hand over a seat whose phone is still at the table", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    r.state.players.find((p) => p.id === "mallory")!.stats.handsPlayed = 1;
    requestSeat(r.state, "c1", "ron");
    addClaimToken(r, "mallory-2", "c1");
    const ronHere = { ron: Date.now() - 5_000 };
    expect(() => resolveClaim(r.state, "mallory", "c1", true, ronHere)).toThrow(/old phone is still at the table/);
    expect(playerIdFor(r, "mallory-2")).toBeNull();
    expect(settle(r, "harry", "c1", true, ronHere)).toBe(true);
    expect(playerIdFor(r, "mallory-2")).toBe("ron");
  });

  it("two lobby phones can't take the host seat while the host is still at the table", () => {
    vi.useFakeTimers();
    const state = createRoom("r", { id: "harry", name: "Harry" }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
    addPlayer(state, { id: "m1", name: "M1" });
    addPlayer(state, { id: "m2", name: "M2" });
    startHand(state);
    for (const p of state.players) p.stats.handsPlayed = 1;
    requestSeat(state, "c1", "harry");
    const harryHere = { harry: Date.now() - 5_000 };
    for (const m of ["m1", "m2"]) expect(() => resolveClaim(state, m, "c1", true, harryHere)).toThrow(/old phone is still at the table/);
    expect(liveClaims(state)).toHaveLength(1);
  });

  it("a locked-out host gets back in with one yes from a player", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "harry-new", "c1");
    expect(settle(r, "ron", "c1", true, { harry: Date.now() - SEAT_AWAY_MS - 1 })).toBe(true);
    expect(playerIdFor(r, "harry-new")).toBe("harry");
  });

  it("someone who hasn't played a hand yet can't let people in, and nobody lets themselves in", () => {
    const r = row();
    addPlayer(r.state, { id: "late", name: "Late" });
    requestSeat(r.state, "c1", "ron");
    expect(canVouch(r.state, "late", r.state.claims![0])).toBe(false);
    expect(() => resolveClaim(r.state, "late", "c1", true)).toThrow(/Play a hand first/);
    expect(() => resolveClaim(r.state, "ron", "c1", true)).toThrow(/can't let yourself in/);
  });

  it("the host key lets the host reclaim without a vouch", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "host-new", "c1");
    const claim = redeemWithHostKey(r.state, "c1", "host-secret");
    expect(claim).toMatchObject({ id: "c1", playerId: "harry" });
    settleClaimToken(r, claim!, true);
    expect(playerIdFor(r, "host-new")).toBe("harry");
    expect(r.state.claims ?? []).toEqual([]);
    expect(redeemWithHostKey(r.state, "c1", "wrong")).toBeNull();
  });

  it("anyone seated can still turn a request away", () => {
    const r = row();
    addPlayer(r.state, { id: "neville", name: "Neville" });
    requestSeat(r.state, "c1", "ron");
    addClaimToken(r, "stranger", "c1");
    expect(settle(r, "neville", "c1", false)).toBe(true);
    expect(playerIdFor(r, "stranger")).toBeNull();
  });

  it("the first deal still freezes trustedIds for diagnostics", () => {
    vi.useFakeTimers();
    const state = createRoom("r", { id: "a", name: "A" }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
    addPlayer(state, { id: "b", name: "B" });
    startHand(state);
    expect(state.trustedIds).toEqual(["a", "b"]);
    addPlayer(state, { id: "c", name: "C" });
    state.hand = null;
    startHand(state);
    expect(state.trustedIds).toEqual(["a", "b"]);
  });
});
