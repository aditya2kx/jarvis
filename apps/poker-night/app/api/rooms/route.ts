import { NextResponse } from "next/server";
import { createRoom } from "@/lib/game";
import { bad, handleError, int, rateLimited, readJson, str } from "@/lib/http";
import { hashToken, insertRoom, newId, newToken } from "@/lib/store";

export async function POST(request: Request) {
  try {
    if (rateLimited(request, 20, 60 * 60 * 1000)) return bad("Too many tables created — try again later", 429);
    const body = await readJson(request);
    const startingChips = int(body.startingChips, "Starting chips", 10, 10_000_000);
    const smallBlind = int(body.smallBlind, "Small blind", 1, startingChips);
    const bigBlind = int(body.bigBlind, "Big blind", smallBlind, startingChips);
    const dealMode = body.dealMode === "app" ? "app" : "physical";
    const blindLevelMinutes = int(body.blindLevelMinutes ?? 0, "Blind level", 0, 240);
    const hostId = newId(8);
    const room = createRoom(
      newId(10),
      { id: hostId, name: str(body.name), avatar: str(body.avatar) },
      { startingChips, smallBlind, bigBlind, dealMode, blindLevelMinutes },
    );
    const token = newToken();
    await insertRoom(room, { [hashToken(token)]: hostId });
    return NextResponse.json({ roomId: room.id, token });
  } catch (error) {
    return handleError(error);
  }
}
