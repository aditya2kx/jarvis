import { NextResponse } from "next/server";
import { addPlayer } from "@/lib/game";
import { handleError, rateLimited, bad, readJson, str } from "@/lib/http";
import { hashToken, mutateRoom, newId, newToken } from "@/lib/store";

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    if (rateLimited(request, 60, 60 * 60 * 1000)) return bad("Too many joins — try again later", 429);
    const { id } = await params;
    const body = await readJson(request);
    const token = newToken();
    const playerId = newId(8);
    const { row } = await mutateRoom(id, (row) => {
      addPlayer(row.state, { id: playerId, name: str(body.name) });
      row.tokens[hashToken(token)] = playerId;
    });
    return NextResponse.json({ token, room: row.state, version: row.version, you: playerId });
  } catch (error) {
    return handleError(error);
  }
}
