import { describe, expect, it } from "vitest";
import { evaluate, fullDeck, shuffle, winChance } from "@/lib/cards";
import { act, createRoom, addPlayer, showCards, startHand, updateSettings, type Room } from "@/lib/game";
import { viewFor } from "@/lib/view";

const name = (cards: string) => evaluate(cards.split(" ")).name;
const score = (cards: string) => evaluate(cards.split(" ")).score;

describe("hand evaluator", () => {
  it.each([
    ["As Ks Qs Js Ts 2d 3c", "Royal flush"],
    ["9h 8h 7h 6h 5h Ac Ad", "Straight flush, Nine high"],
    ["Ah 2h 3h 4h 5h Kc Kd", "Straight flush, Five high"],
    ["7c 7d 7h 7s Ks 2d 3c", "Four of a kind, Sevens"],
    ["Kc Kd Kh 7s 7d 2d 3c", "Full house, Kings full of Sevens"],
    ["Kc Kd Kh 7s 7d 7h 3c", "Full house, Kings full of Sevens"],
    ["Ah 9h 7h 4h 2h Kc Qd", "Flush, Ace high"],
    ["Ac 2d 3h 4s 5c Kd 9h", "Straight, Five high"],
    ["Tc Jd Qh Ks Ac 2d 3h", "Straight, Ace high"],
    ["8c 8d 8h Ks 2c 4d 6h", "Three of a kind, Eights"],
    ["Ac Ad 5h 5s Kc Kd 2h", "Two pair, Aces and Kings"],
    ["Jc Jd 5h 3s 9c 2d 7h", "Pair of Jacks"],
    ["Ac Jd 5h 3s 9c 2d 7h", "High card, Ace"],
  ])("%s → %s", (cards, expected) => {
    expect(name(cards)).toBe(expected);
  });

  it("orders hands and kickers correctly", () => {
    expect(score("Ah Ad Kc 7s 2d 3c 9h")).toBeGreaterThan(score("Ah Ad Qc 7s 2d 3c 9h"));
    expect(score("2c 3c 4c 5c 7c Ad Ah")).toBeGreaterThan(score("Tc Jd Qh Ks Ac 2d 3h"));
    expect(score("Kc Kd Kh 2s 2d 3c 4h")).toBeGreaterThan(score("Ah 9h 7h 4h 2h Kc Qd"));
    expect(score("Ah 2d 3c 4s 5h 9c Td")).toBeLessThan(score("2h 3d 4c 5s 6h 9c Td"));
    expect(score("As Kd 5c 5s 9h 2c 3d")).toBe(score("Ah Kc 5d 5h 9s 2d 3c"));
  });

  it("shuffle returns a full unique deck", () => {
    const deck = shuffle(fullDeck());
    expect(new Set(deck).size).toBe(52);
  });

  it("win chance is sensible", () => {
    expect(winChance(["As", "Ah"], [], 1, 3000)).toBeGreaterThan(0.78);
    expect(winChance(["7c", "2d"], [], 1, 3000)).toBeLessThan(0.4);
  });
});

function appTable(names: string[]): Room {
  const room = createRoom("r", { id: names[0], name: names[0] }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "app", blindLevelMinutes: 0 });
  for (const n of names.slice(1)) addPlayer(room, { id: n, name: n });
  return room;
}

describe("in-app dealing", () => {
  it("deals two unique cards each plus a hidden board", () => {
    const room = appTable(["A", "B", "C", "D", "E"]);
    startHand(room);
    const h = room.hand!;
    const all = [...Object.values(h.holes!).flat(), ...h.board!, ...h.deck!];
    expect(new Set(all).size).toBe(52);
    expect(Object.values(h.holes!).every((c) => c.length === 2)).toBe(true);
  });

  it("each viewer only sees their own cards and the dealt board", () => {
    const room = appTable(["A", "B", "C"]);
    startHand(room);
    const v = viewFor(room, "B");
    expect(Object.keys(v.hand!.holes!)).toEqual(["B"]);
    expect(v.hand!.board).toEqual([]);
    expect(v.hand!.deck).toBeUndefined();
    expect(viewFor(room, null).hand!.holes).toEqual({});
    act(room, "A", { type: "call" });
    act(room, "B", { type: "call" });
    act(room, "C", { type: "check" });
    expect(viewFor(room, "A").hand!.board).toHaveLength(3);
    expect(room.hand!.board).toHaveLength(5);
  });

  it("finds the winner automatically at showdown and reveals live hands", () => {
    const room = appTable(["A", "B"]);
    startHand(room);
    room.hand!.holes = { A: ["As", "Ah"], B: ["7c", "2d"] };
    room.hand!.board = ["Ad", "Kc", "9s", "4h", "3c"];
    act(room, "A", { type: "allin" });
    act(room, "B", { type: "call" });
    expect(room.hand).toBeNull();
    const r = room.lastResult!;
    expect(r.winners).toEqual([{ id: "A", amount: 2000, handName: "Three of a kind, Aces" }]);
    expect(r.shown.map((s) => s.id)).toEqual(["A", "B"]);
    expect(room.players[0].stats).toMatchObject({ biggestPot: 2000, streak: 1, bestHand: "Three of a kind, Aces", allIns: 1 });
    expect(viewFor(room, "B").lastHoles).toBeUndefined();
  });

  it("splits a chopped board", () => {
    const room = appTable(["A", "B"]);
    startHand(room);
    room.hand!.holes = { A: ["2s", "3h"], B: ["2c", "3d"] };
    room.hand!.board = ["As", "Ks", "Qd", "Jh", "Tc"];
    act(room, "A", { type: "call" });
    act(room, "B", { type: "check" });
    for (let i = 0; i < 3; i++) {
      act(room, "B", { type: "check" });
      act(room, "A", { type: "check" });
    }
    expect(room.players.map((p) => p.chips)).toEqual([1000, 1000]);
  });

  it("winner can show a bluff after everyone folds", () => {
    const room = appTable(["A", "B"]);
    startHand(room);
    const aCards = room.hand!.holes!.A;
    act(room, "A", { type: "raise", to: 100 });
    act(room, "B", { type: "fold" });
    showCards(room, "A");
    expect(room.lastResult!.shown[0]).toMatchObject({ id: "A", cards: aCards });
  });
});

describe("settings", () => {
  it("blind levels double when the timer runs out", () => {
    const room = appTable(["A", "B"]);
    updateSettings(room, { smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 15 });
    room.levelStartedAt -= 16 * 60_000;
    startHand(room);
    expect(room.settings).toMatchObject({ smallBlind: 20, bigBlind: 40 });
    expect(room.blindLevel).toBe(2);
    expect(room.hand!.holes).toBeUndefined();
  });

  it("deal mode can't change mid-hand", () => {
    const room = appTable(["A", "B"]);
    startHand(room);
    expect(() => updateSettings(room, { smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 })).toThrow(/between hands/);
  });
});
