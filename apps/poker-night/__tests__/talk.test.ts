import { afterEach, describe, expect, it, vi } from "vitest";
import { act, addPlayer, chat, CHAT_MAX_LENGTH, createRoom, startHand, type Room } from "@/lib/game";
import { viewFor } from "@/lib/view";

function table(names: string[]): Room {
  const room = createRoom("r", { id: names[0], name: names[0] }, { startingChips: 1000, smallBlind: 10, bigBlind: 20, dealMode: "physical", blindLevelMinutes: 0 });
  for (const n of names.slice(1)) addPlayer(room, { id: n, name: n });
  return room;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("last move per seat", () => {
  it("shows the blinds as soon as a hand starts", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    const m = room.hand!.moves!;
    expect([m.B.kind, m.B.amount, m.C.kind, m.C.amount]).toEqual(["sb", 10, "bb", 20]);
    expect(room.hand!.lastMove).toMatchObject({ id: "C", kind: "bb" });
  });

  it("records each action and clears the tags when the street changes", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "raise", to: 60 });
    act(room, "B", { type: "fold" });
    act(room, "C", { type: "call" });
    const pre = room.hand!;
    expect(pre.street).toBe("flop");
    expect(pre.moves).toEqual({});
    expect(pre.lastMove).toMatchObject({ id: "C", kind: "call", amount: 40 });

    act(room, "C", { type: "raise", to: 100 });
    expect(room.hand!.moves!.C).toMatchObject({ kind: "bet", amount: 100 });
    act(room, "A", { type: "raise", to: 300 });
    expect(room.hand!.moves!.A).toMatchObject({ kind: "raise", amount: 300 });
    act(room, "C", { type: "allin" });
    expect(room.hand!.moves!.C).toMatchObject({ kind: "allin", amount: 940 });
  });

  it("labels a call that uses every last chip as all in", () => {
    const room = table(["A", "B"]);
    room.players[1].chips = 50;
    startHand(room);
    act(room, "A", { type: "raise", to: 200 });
    act(room, "B", { type: "call" });
    expect(room.hand!.lastMove).toMatchObject({ id: "B", kind: "allin", amount: 50 });
  });
});

describe("chat", () => {
  it("cleans and caps messages", () => {
    const room = table(["A", "B"]);
    chat(room, "A", "  you\u202E   are\n\tbluffing  ");
    chat(room, "B", "x".repeat(500));
    expect(room.chat!.map((m) => m.text)).toEqual(["you are bluffing", "x".repeat(CHAT_MAX_LENGTH)]);
  });

  it("rejects empty messages and people who aren't seated", () => {
    const room = table(["A"]);
    expect(() => chat(room, "A", " \n ")).toThrow(/Type something/);
    expect(() => chat(room, "ghost", "hi")).toThrow(/Unknown player/);
  });

  it("slows down spam per player but not for everyone else", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const room = table(["A", "B"]);
    for (let i = 0; i < 4; i++) chat(room, "A", `msg ${i}`);
    expect(() => chat(room, "A", "one more")).toThrow(/one sec/);
    chat(room, "B", "lol");
    vi.setSystemTime(1_009_000);
    chat(room, "A", "back");
    expect(room.chat!.length).toBe(6);
  });

  it("keeps only the latest 60 messages", () => {
    vi.useFakeTimers();
    const room = table(["A"]);
    for (let i = 0; i < 70; i++) {
      vi.setSystemTime(2_000_000 + i * 10_000);
      chat(room, "A", `m${i}`);
    }
    expect(room.chat!.length).toBe(60);
    expect(room.chat![0].text).toBe("m10");
  });

  it("a hand already in progress when the new version deploys plays on", () => {
    const room = table(["A", "B", "C"]);
    startHand(room);
    act(room, "A", { type: "call" });
    const saved = JSON.parse(JSON.stringify(room)) as Room;
    delete saved.chat;
    delete saved.hand!.moves;
    delete saved.hand!.lastMove;

    expect(viewFor(saved, "B").hand!.moves).toBeUndefined();
    act(saved, "B", { type: "call" });
    expect(saved.hand!.moves).toEqual({ B: expect.objectContaining({ kind: "call", amount: 10 }) });
    act(saved, "C", { type: "check" });
    expect(saved.hand!.street).toBe("flop");
    chat(saved, "A", "still here");
    expect(saved.players.map((p) => p.chips)).toEqual([980, 980, 980]);
  });

  it("rooms saved before chat existed still load with an empty feed", () => {
    const room = table(["A"]);
    delete room.chat;
    expect(viewFor(room, "A").chat).toEqual([]);
    chat(room, "A", "first!");
    expect(room.chat!.length).toBe(1);
  });
});
