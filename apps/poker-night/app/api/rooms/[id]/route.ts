import { NextResponse } from "next/server";
import { BUILD } from "@/lib/build";
import { bad, handleError, pollSince, tokenFrom } from "@/lib/http";
import { loadRoom, playerIdFor } from "@/lib/store";
import { viewFor } from "@/lib/view";
import { voiceMembers } from "@/lib/voice-store";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const row = await loadRoom(id);
    if (!row) return bad("Table not found", 404);
    const since = pollSince(new URL(request.url).searchParams.get("v"));
    const you = playerIdFor(row, tokenFrom(request));
    // Voice presence lives outside the versioned room state, so it rides along even on "unchanged".
    const voice = (await voiceMembers(id)).map(({ id, muted }) => ({ id, muted }));
    if (Number.isFinite(since) && since === row.version) {
      return NextResponse.json({ unchanged: true, version: row.version, you, now: Date.now(), build: BUILD, voice });
    }
    return NextResponse.json({ room: viewFor(row.state, you), version: row.version, you, now: Date.now(), build: BUILD, voice });
  } catch (error) {
    return handleError(error);
  }
}
