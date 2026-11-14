import { NextResponse } from "next/server";
import { bad, handleError, pollSince, tokenFrom } from "@/lib/http";
import { loadRoom, playerIdFor } from "@/lib/store";
import { viewFor } from "@/lib/view";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const row = await loadRoom(id);
    if (!row) return bad("Table not found", 404);
    const since = pollSince(new URL(request.url).searchParams.get("v"));
    const you = playerIdFor(row, tokenFrom(request));
    if (Number.isFinite(since) && since === row.version) {
      return NextResponse.json({ unchanged: true, version: row.version, you, now: Date.now() });
    }
    return NextResponse.json({ room: viewFor(row.state, you), version: row.version, you, now: Date.now() });
  } catch (error) {
    return handleError(error);
  }
}
