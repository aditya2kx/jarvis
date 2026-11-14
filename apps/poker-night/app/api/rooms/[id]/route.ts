import { NextResponse } from "next/server";
import { bad, handleError, tokenFrom } from "@/lib/http";
import { loadRoom, playerIdFor } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const row = await loadRoom(id);
    if (!row) return bad("Table not found", 404);
    const since = Number(new URL(request.url).searchParams.get("v"));
    const you = playerIdFor(row, tokenFrom(request));
    if (since === row.version) return NextResponse.json({ unchanged: true, version: row.version, you });
    return NextResponse.json({ room: row.state, version: row.version, you });
  } catch (error) {
    return handleError(error);
  }
}
