import { NextResponse } from "next/server";
import { GameError } from "@/lib/game";
import { bad, handleError, readJson, str, tokenFrom } from "@/lib/http";
import { loadRoom, playerIdFor } from "@/lib/store";
import { isNonce, joinVoice, leaveVoice, SDP_MAX_LENGTH, sendSignal, signalsFor, touchVoice, voiceMembers } from "@/lib/voice-store";

export const dynamic = "force-dynamic";

/** Voice is for people holding a seat; spectators with the link can't listen in or signal anyone. */
async function seated(request: Request, id: string) {
  const row = await loadRoom(id);
  if (!row) throw new GameError("Table not found");
  const me = playerIdFor(row, tokenFrom(request));
  if (!me || !row.state.players.some((p) => p.id === me)) return null;
  return { me, seats: new Set(row.state.players.map((p) => p.id)) };
}

async function members(id: string, seats: Set<string>) {
  return (await voiceMembers(id)).filter((m) => seats.has(m.id));
}

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const who = await seated(request, id);
    if (!who) return bad("Take a seat to use voice", 403);
    const q = new URL(request.url).searchParams;
    const n = q.get("n");
    const after = /^\d{1,18}$/.test(q.get("after") ?? "") ? q.get("after")! : "0";
    const joined = isNonce(n) && (await touchVoice(id, who.me, n));
    const signals = joined ? await signalsFor(id, who.me, after) : [];
    return NextResponse.json({ joined, members: await members(id, who.seats), signals });
  } catch (error) {
    return handleError(error);
  }
}

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    const who = await seated(request, id);
    if (!who) return bad("Take a seat to use voice", 403);
    const body = await readJson(request);
    const n = body.n;
    if (!isNonce(n)) return bad("Invalid voice session");
    const muted = body.muted === true;
    switch (str(body.action)) {
      case "join":
        await joinVoice(id, who.me, n, muted);
        break;
      case "leave":
        await leaveVoice(id, who.me, n);
        break;
      case "mute":
        if (!(await touchVoice(id, who.me, n, muted))) return bad("You left voice — tap the mic to rejoin");
        break;
      case "signal": {
        const type = body.type;
        const sdp = body.sdp;
        const to = str(body.to);
        if (type !== "offer" && type !== "answer") return bad("Invalid voice message");
        if (typeof sdp !== "string" || sdp.length === 0 || sdp.length > SDP_MAX_LENGTH) return bad("Invalid voice message");
        if (!isNonce(body.toN) || to === who.me) return bad("Invalid voice message");
        if (!(await touchVoice(id, who.me, n))) return bad("You left voice — tap the mic to rejoin");
        const peer = (await members(id, who.seats)).find((m) => m.id === to);
        if (!peer || peer.n !== body.toN) return bad("They left voice");
        await sendSignal(id, who.me, to, { type, sdp, n, toN: body.toN });
        break;
      }
      default:
        return bad("Unknown voice action");
    }
    return NextResponse.json({ ok: true, members: await members(id, who.seats) });
  } catch (error) {
    return handleError(error);
  }
}
