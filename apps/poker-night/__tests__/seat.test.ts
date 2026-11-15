import { afterEach, describe, expect, it, vi } from "vitest";
import { addPlayer, canVouch, CLAIM_TTL_MS, createRoom, requestSeat, resolveClaim, type Room } from "@/lib/game";
import { addClaimToken, hashToken, playerIdFor, settleClaimToken } from "@/lib/store";
import { viewFor } from "@/lib/view";

vi.mock("@/lib/db", () => ({ sql: vi.fn() }));

function row() {
  vi.useFakeTimers();
  const state: Room = createRoom("r", { id: "harry", name: "Harry" }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
  addPlayer(state, { id: "ron", name: "Ron" });
  for (const p of state.players) p.stats.handsPlayed = 3;
  vi.advanceTimersByTime(60_000);
  return { state, tokens: { [hashToken("harry-old")]: "harry", [hashToken("ron-tok")]: "ron" } as Record<string, string>, version: 1 };
}

afterEach(() => vi.useRealTimers());

describe("getting a lost seat back", () => {
  it("a pending request token is not a seat until someone at the table allows it", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "new-phone", "c1");
    expect(playerIdFor(r, "new-phone")).toBeNull();
    expect(viewFor(r.state, null).claims).toEqual([expect.objectContaining({ id: "c1", playerId: "harry" })]);

    settleClaimToken(r, resolveClaim(r.state, "ron", "c1", true), true);
    expect(playerIdFor(r, "new-phone")).toBe("harry");
    expect(r.state.hostId).toBe("harry");
    expect(r.state.players).toHaveLength(2);
    expect(viewFor(r.state, "harry").claims).toEqual([]);
  });

  it("turning a request down drops its token", () => {
    const r = row();
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "stranger", "c1");
    settleClaimToken(r, resolveClaim(r.state, "harry", "c1", false), false);
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

  it("a newcomer can't approve: the take-over-the-host attack is refused", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    vi.advanceTimersByTime(1000);
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "mallory-2", "c1");
    expect(() => resolveClaim(r.state, "mallory", "c1", true)).toThrow(/already playing/);
    expect(playerIdFor(r, "mallory-2")).toBeNull();
    expect(viewFor(r.state, null).claims).toHaveLength(1);
  });

  it("a seat that joined before the request but hasn't played a hand can't approve either", () => {
    const r = row();
    addPlayer(r.state, { id: "mallory", name: "Mallory" });
    vi.advanceTimersByTime(5 * 60_000);
    requestSeat(r.state, "c1", "harry");
    expect(canVouch(r.state, "mallory", r.state.claims![0])).toBe(false);
    expect(canVouch(r.state, "ron", r.state.claims![0])).toBe(true);
    expect(() => resolveClaim(r.state, "mallory", "c1", true)).toThrow(/already playing/);
  });

  it("anyone seated can still turn a request away", () => {
    const r = row();
    addPlayer(r.state, { id: "neville", name: "Neville" });
    requestSeat(r.state, "c1", "harry");
    addClaimToken(r, "stranger", "c1");
    settleClaimToken(r, resolveClaim(r.state, "neville", "c1", false), false);
    expect(playerIdFor(r, "stranger")).toBeNull();
  });

  it("players saved before seat recovery existed count as long seated", () => {
    const r = row();
    delete r.state.players.find((p) => p.id === "ron")!.joinedAt;
    requestSeat(r.state, "c1", "harry");
    expect(canVouch(r.state, "ron", r.state.claims![0])).toBe(true);
  });
});
