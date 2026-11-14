import { describe, expect, it } from "vitest";
import { act, addPlayer, cancelHand, createRoom, pots, rebuy, resolveShowdown, startHand, type Room } from "@/lib/game";

function table(names: string[], chips = 1000): Room {
  const room = createRoom("r", { id: names[0], name: names[0] }, { startingChips: chips, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
  for (const n of names.slice(1)) addPlayer(room, { id: n, name: n });
  return room;
}

const total = (room: Room) =>
  room.players.reduce((s, p) => s + p.chips, 0) + Object.values(room.hand?.contributed ?? {}).reduce((a, b) => a + b, 0);

describe("blinds and turn order", () => {
  it("posts blinds left of the dealer and starts with under-the-gun", () => {
    const room = table(["A", "B", "C", "D", "E"]);
    startHand(room);
    const h = room.hand!;
    expect([h.dealerId, h.sbId, h.bbId, h.toAct]).toEqual(["A", "B", "C", "D"]);
    expect(h.contributed).toEqual({ B: 10, C: 20 });
  });

  it("rotates the dealer button each hand", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    cancelHand(room);
    startHand(room);
    expect(room.hand!.dealerId).toBe("B");
  });

  it("heads-up: dealer posts the small blind and acts first preflop, last after", () => {
    const room = table(["A", "B"]);
    startHand(room);
    expect([room.hand!.sbId, room.hand!.bbId, room.hand!.toAct]).toEqual(["A", "B", "A"]);
    act(room, "A", { type: "call" });
    act(room, "B", { type: "check" });
    expect(room.hand!.street).toBe("flop");
    expect(room.hand!.toAct).toBe("B");
  });
});

describe("betting", () => {
  it("gives the big blind an option, then moves to the flop", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "call" });
    act(room, "B", { type: "call" });
    expect(room.hand!.toAct).toBe("C");
    act(room, "C", { type: "check" });
    expect(room.hand!.street).toBe("flop");
    expect(room.hand!.toAct).toBe("B");
  });

  it("reopens action after a raise and enforces the minimum raise", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    expect(() => act(room, "A", { type: "raise", to: 30 })).toThrow(/Minimum raise is to 40/);
    act(room, "A", { type: "raise", to: 60 });
    act(room, "B", { type: "call" });
    act(room, "C", { type: "raise", to: 200 });
    expect(room.hand!.toAct).toBe("A");
    act(room, "A", { type: "call" });
    act(room, "B", { type: "fold" });
    expect(room.hand!.street).toBe("flop");
    expect(total(room)).toBe(3000);
  });

  it("rejects out-of-turn actions", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    expect(() => act(room, "B", { type: "call" })).toThrow(/A's turn/);
  });

  it("awards the pot when everyone else folds", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "fold" });
    act(room, "B", { type: "fold" });
    expect(room.hand).toBeNull();
    expect(room.players.find((p) => p.id === "C")!.chips).toBe(1010);
    expect(room.lastResult?.text).toMatch(/C wins 30/);
  });

  it("plays a full hand through every street to showdown", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "call" });
    act(room, "B", { type: "call" });
    act(room, "C", { type: "check" });
    for (const street of ["flop", "turn", "river"]) {
      expect(room.hand!.street).toBe(street);
      act(room, "B", { type: "check" });
      act(room, "C", { type: "check" });
      act(room, "A", { type: "check" });
    }
    expect(room.hand!.street).toBe("showdown");
    resolveShowdown(room, [["B", "C"]]);
    expect(room.players.map((p) => p.chips)).toEqual([980, 1010, 1010]);
  });
});

describe("all-ins and side pots", () => {
  it("builds a main pot and side pot and skips to showdown", () => {
    const room = table(["A", "B", "C"]);
    room.players[0].chips = 100;
    startHand(room);
    act(room, "A", { type: "allin" });
    act(room, "B", { type: "raise", to: 500 });
    act(room, "C", { type: "call" });
    expect(room.hand!.street).toBe("flop");
    act(room, "B", { type: "allin" });
    act(room, "C", { type: "call" });
    expect(room.hand!.street).toBe("showdown");
    const ps = pots(room.hand!);
    expect(ps).toEqual([
      { amount: 300, eligible: ["A", "B", "C"] },
      { amount: 1800, eligible: ["B", "C"] },
    ]);
    resolveShowdown(room, [["A"], ["C"]]);
    expect(room.players.map((p) => p.chips)).toEqual([300, 0, 1800]);
  });

  it("folded chips stay in the pot but folded players are not eligible", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "raise", to: 100 });
    act(room, "B", { type: "fold" });
    act(room, "C", { type: "allin" });
    act(room, "A", { type: "call" });
    expect(pots(room.hand!)).toEqual([{ amount: 2010, eligible: ["A", "C"] }]);
  });

  it("splits odd chips starting left of the dealer", () => {
    const room = table(["A", "B", "C"]);
    room.settings.smallBlind = 5;
    room.settings.bigBlind = 15;
    startHand(room);
    act(room, "A", { type: "call" });
    act(room, "B", { type: "fold" });
    act(room, "C", { type: "check" });
    for (let i = 0; i < 3; i++) {
      act(room, "C", { type: "check" });
      act(room, "A", { type: "check" });
    }
    expect(pots(room.hand!)[0].amount).toBe(35);
    resolveShowdown(room, [["A", "C"]]);
    expect(room.players.map((p) => p.chips)).toEqual([1002, 995, 1003]);
  });

  it("blinds that put players all in go straight to showdown", () => {
    const room = table(["A", "B"]);
    room.players.forEach((p) => (p.chips = 10));
    startHand(room);
    expect(room.hand!.street).toBe("showdown");
  });
});

describe("table management", () => {
  it("rebuy tracks buy-ins for the net score", () => {
    const room = table(["A", "B"]);
    rebuy(room, "B");
    expect(room.players[1]).toMatchObject({ chips: 2000, boughtIn: 2000 });
  });

  it("cancel returns every chip", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "raise", to: 300 });
    cancelHand(room);
    expect(room.players.map((p) => p.chips)).toEqual([1000, 1000, 1000]);
  });

  it("rejects duplicate names", () => {
    const room = table(["Adi"]);
    expect(() => addPlayer(room, { id: "x", name: " adi " })).toThrow(/already has that name/);
  });
});
