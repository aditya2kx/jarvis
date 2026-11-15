import { afterEach, describe, expect, it, vi } from "vitest";
import { addPlayer, canVouch, CLAIM_TTL_MS, createRoom, requestSeat, resolveClaim, startHand, trustedIds, type Room } from "@/lib/game";
import { addClaimToken, hashToken, playerIdFor, settleClaimToken } from "@/lib/store";
import { viewFor } from "@/lib/view";

vi.mock("@/lib/db", () => ({ sql: vi.fn() }));

function row() {
  vi.useFakeTimers();
  const state: Room = createRoom("r", { id: "harry", name: "Harry" }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
  addPlayer(state, { id: "ron", name: "Ron" });
  for (const p of state.players) p.stats.handsPlayed = 3;
  state.trustedIds = ["harry", "ron"];
  state.handCount = 1;
  vi.advanceTimersByTime(60_000);
  return { state, tokens: { [hashToken("harry-old")]: "harry", [hashToken("ron-tok")]: "ron" } as Record<string, string>, version: 1 };
}

function settle(r: ReturnType<typeof row>, by: string, claimId: string, allow: boolean) {
  const { claim, done } = resolveClaim(r.state, by, claimId, allow);
  if (done) settleClaimToken(r, claim, allow);
  return done;
}

afterEach(() => vi.useRealTimers());

describe("getting a lost seat back", () => {
  it("a pending request token is not a seat until someone at the table allows it", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "new-phone", "c1");
    expect(playerIdFor(r, "new-phone")).toBeNull();
    expect(viewFor(r.state, null).claims).toEqual([expect.objectContaining({ id: "c1", playerId: "harry" })]);

    expect(settle(r, "ron", "c1", true)).toBe(true);
    expect(playerIdFor(r, "new-phone")).toBe("harry");
    expect(r.state.hostId).toBe("harry");
    expect(r.state.players).toHaveLength(2);
    expect(viewFor(r.state, "harry").claims).toEqual([]);
  });

  it("turning a request down drops its token", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "stranger", "c1");
    expect(settle(r, "harry", "c1", false)).toBe(true);
    expect(playerIdFor(r, "stranger")).toBeNull();
    expect(Object.values(r.tokens)).toEqual(["harry", "ron"]);
  });

  it("requests expire, can't be approved late, and their tokens get swept", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "slow", "c1");
    vi.advanceTimersByTime(CLAIM_TTL_MS + 1);
    expect(viewFor(r.state, "ron").claims).toEqual([]);
    expect(() => resolveClaim(r.state, "ron", "c1", true)).toThrow(/expired/);
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

  it("a late joiner who plays a hand still can't approve (review attack)", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    r.state.players.find((p) => p.id === "mallory")!.stats.handsPlayed = 5;
    vi.advanceTimersByTime(1000);
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "mallory-2", "c1");
    expect(trustedIds(r.state)).toEqual(["harry", "ron"]);
    expect(canVouch(r.state, "mallory", r.state.claims![0])).toBe(false);
    expect(() => resolveClaim(r.state, "mallory", "c1", true)).toThrow(/first hand/);
    expect(playerIdFor(r, "mallory-2")).toBeNull();
  });

  it("when three opened together, one founder's yes is not enough — blocks the two-phone attack", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    r.state.trustedIds = ["harry", "ron", "mallory"];
    for (const p of r.state.players) p.stats.handsPlayed = 3;
    vi.advanceTimersByTime(1000);
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "attacker", "c1");
    expect(settle(r, "mallory", "c1", true)).toBe(false);
    expect(playerIdFor(r, "attacker")).toBeNull();
    expect(viewFor(r.state, null).claims![0].approvals).toEqual(["mallory"]);
    expect(settle(r, "ron", "c1", true)).toBe(true);
    expect(playerIdFor(r, "attacker")).toBe("harry");
  });

  it("a seat that joined before the request but wasn't in the first hand can't approve", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    vi.advanceTimersByTime(5 * 60_000);
    requestSeat(r.state, "c1", "harry");
    expect(canVouch(r.state, "mallory", r.state.claims![0])).toBe(false);
    expect(canVouch(r.state, "ron", r.state.claims![0])).toBe(true);
  });

  it("anyone seated can still turn a request away", () => {
    const r = row();
    addPlayer(r.state, { id: "neville", name: "Neville" });
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "stranger", "c1");
    expect(settle(r, "neville", "c1", false)).toBe(true);
    expect(playerIdFor(r, "stranger")).toBeNull();
  });

  it("players saved before seat recovery existed count as long seated", () => {
    const r = row();
    delete r.state.players.find((p) => p.id === "ron")!.joinedAt;
    requestSeat(r.state, "c1", "harry");
    expect(canVouch(r.state, "ron", r.state.claims![0])).toBe(true);
  });

  it("the first deal freezes trustedIds so late joiners never get in", () => {
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
