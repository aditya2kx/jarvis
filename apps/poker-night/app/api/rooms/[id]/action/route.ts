import { NextResponse } from "next/server";
import {
  act,
  adjustChips,
  cancelHand,
  GameError,
  react,
  rebuy,
  removePlayer,
  resolveShowdown,
  setSittingOut,
  showCards,
  startHand,
  updateSettings,
  type Action,
  type Room,
} from "@/lib/game";
import { bad, handleError, int, readJson, str, tokenFrom } from "@/lib/http";
import { mutateRoom, playerIdFor } from "@/lib/store";
import { viewFor } from "@/lib/view";

function parseAction(body: Record<string, unknown>): Action {
  const move = str(body.move);
  switch (move) {
    case "fold":
    case "check":
    case "call":
    case "allin":
      return { type: move };
    case "raise":
      return { type: "raise", to: int(body.to, "Amount", 1, 1e9) };
    default:
      throw new GameError("Unknown move");
  }
}

function parseWinners(value: unknown): string[][] {
  if (!Array.isArray(value) || value.length > 12) throw new GameError("Pick the winners");
  return value.map((pot) => {
    if (!Array.isArray(pot) || pot.length > 12) throw new GameError("Pick the winners");
    return pot.map(str);
  });
}

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const body = await readJson(request);
    const token = tokenFrom(request);
    const { row, result: me } = await mutateRoom(id, (row) => {
      const me = playerIdFor(row, token);
      if (!me || !row.state.players.some((p) => p.id === me)) throw new GameError("Join the table first");
      apply(row.state, me, body);
      return me;
    });
    return NextResponse.json({ room: viewFor(row.state, me), version: row.version, you: me, now: Date.now() });
  } catch (error) {
    if (error instanceof GameError && error.message === "Join the table first") return bad(error.message, 403);
    return handleError(error);
  }
}

function apply(room: Room, me: string, body: Record<string, unknown>) {
  const isHost = me === room.hostId;
  const target = str(body.playerId) || me;
  const requireHost = () => {
    if (!isHost) throw new GameError("Only the host can do that");
  };
  const kind = str(body.kind);
  switch (kind) {
    case "start":
      return startHand(room);
    case "act":
      if (target !== me) requireHost();
      return act(room, target, parseAction(body));
    case "showdown":
      return resolveShowdown(room, parseWinners(body.winners));
    case "show":
      return showCards(room, me);
    case "react":
      return react(room, me, str(body.emoji));
    case "rebuy":
      if (target !== me) requireHost();
      return rebuy(room, target);
    case "sitout":
      if (target !== me) requireHost();
      return setSittingOut(room, target, body.sittingOut === true);
    case "cancel":
      requireHost();
      return cancelHand(room);
    case "adjust":
      requireHost();
      return adjustChips(room, target, int(body.chips, "Chips", 0, 1e9));
    case "remove":
      requireHost();
      return removePlayer(room, target);
    case "settings":
      requireHost();
      return updateSettings(room, {
        smallBlind: int(body.smallBlind, "Small blind", 1, 1e7),
        bigBlind: int(body.bigBlind, "Big blind", 1, 1e7),
        dealMode: body.dealMode === "app" ? "app" : "physical",
        blindLevelMinutes: int(body.blindLevelMinutes ?? 0, "Blind level", 0, 240),
      });
    default:
      throw new GameError("Unknown request");
  }
}
