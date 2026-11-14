import { NextResponse } from "next/server";
import { GameError } from "./game";

export function bad(message: string, status = 400) {
  return NextResponse.json({ error: message }, { status });
}

export function handleError(error: unknown) {
  if (error instanceof GameError) return bad(error.message);
  console.error("poker-night request failed", error instanceof Error ? error.message : "unknown error");
  return bad("Something went wrong", 500);
}

export function tokenFrom(request: Request) {
  return request.headers.get("x-player-token");
}

export async function readJson(request: Request): Promise<Record<string, unknown>> {
  const text = await request.text();
  if (text.length > 10_000) throw new GameError("Request too large");
  try {
    const body = JSON.parse(text || "{}");
    return body && typeof body === "object" ? body : {};
  } catch {
    throw new GameError("Invalid request");
  }
}

export function int(value: unknown, label: string, min: number, max: number) {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(n) || n < min || n > max) throw new GameError(`${label} must be a whole number from ${min} to ${max}`);
  return n;
}

export function str(value: unknown) {
  return typeof value === "string" ? value : "";
}

const hits = new Map<string, number[]>();

/** Small in-memory limiter: good enough to stop a single client from spamming table creation. */
export function rateLimited(request: Request, limit: number, windowMs: number) {
  const key = request.headers.get("x-forwarded-for")?.split(",")[0].trim() || "local";
  const now = Date.now();
  const recent = (hits.get(key) ?? []).filter((t) => now - t < windowMs);
  recent.push(now);
  hits.set(key, recent);
  if (hits.size > 5000) hits.clear();
  return recent.length > limit;
}
