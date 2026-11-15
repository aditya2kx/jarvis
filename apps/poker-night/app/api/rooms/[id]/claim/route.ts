import { NextResponse } from "next/server";
import { GameError, redeemWithHostKey, requestSeat } from "@/lib/game";
import { bad, handleError, rateLimited, readJson, str, tokenFrom } from "@/lib/http";
import { addClaimToken, mutateRoom, newId, newToken, playerIdFor, settleClaimToken } from "@/lib/store";
import { viewFor } from "@/lib/view";

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    if (rateLimited(request, 30, 60 * 60 * 1000)) return bad("Too many seat requests — try again later", 429);
    const { id } = await params;
    const body = await readJson(request);
    const token = newToken();
    const claimId = newId(10);
    const hostKey = typeof body.hostKey === "string" ? body.hostKey : "";
    const { row } = await mutateRoom(id, (row) => {
      if (playerIdFor(row, tokenFrom(request))) throw new GameError("You already have a seat at this table");
      requestSeat(row.state, claimId, str(body.playerId));
      addClaimToken(row, token, claimId);
      const redeemed = hostKey ? redeemWithHostKey(row.state, claimId, hostKey) : null;
      if (redeemed) settleClaimToken(row, redeemed, true);
    });
    const me = playerIdFor(row, token);
    return NextResponse.json({
      token,
      claimId,
      room: viewFor(row.state, me),
      version: row.version,
      you: me,
      now: Date.now(),
    });
  } catch (error) {
    return handleError(error);
  }
}
