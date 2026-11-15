"use client";

import type { Room } from "./game";

export type VoicePresence = { id: string; muted: boolean };

export type RoomResponse = { room?: Room; version: number; you: string | null; now: number; unchanged?: boolean; error?: string; build?: string; voice?: VoicePresence[] };

/** A newer deploy is live. Reload only when nothing local would be lost: not your turn, nothing typed, no request in flight. */
export function safeToReload(opts: { myTurn: boolean; busy: boolean }) {
  if (opts.myTurn || opts.busy) return false;
  const el = document.activeElement;
  return !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) || el.value.trim() === "";
}

const key = (roomId: string) => `poker-night:${roomId}`;

export function savedToken(roomId: string) {
  return typeof window === "undefined" ? null : localStorage.getItem(key(roomId));
}

export function saveToken(roomId: string, token: string) {
  localStorage.setItem(key(roomId), token);
}

export function forgetToken(roomId: string) {
  localStorage.removeItem(key(roomId));
  localStorage.removeItem(`${key(roomId)}:claim`);
  // Keep hostKey so the host can reclaim after clearing the seat token.
}

export function savedHostKey(roomId: string) {
  return typeof window === "undefined" ? null : localStorage.getItem(`${key(roomId)}:hostKey`);
}

export function saveHostKey(roomId: string, hostKey: string) {
  localStorage.setItem(`${key(roomId)}:hostKey`, hostKey);
}

/** The seat this phone asked for, so a refresh while waiting keeps showing the request. */
export type PendingClaim = { claimId: string; playerId: string };

export function savedClaim(roomId: string): PendingClaim | null {
  try {
    return JSON.parse(localStorage.getItem(`${key(roomId)}:claim`) ?? "null");
  } catch {
    return null;
  }
}

export function saveClaim(roomId: string, claim: PendingClaim | null) {
  if (claim) localStorage.setItem(`${key(roomId)}:claim`, JSON.stringify(claim));
  else localStorage.removeItem(`${key(roomId)}:claim`);
}

export function loadPref(name: string, fallback: boolean) {
  if (typeof window === "undefined") return fallback;
  const v = localStorage.getItem(`poker-night:pref:${name}`);
  return v === null ? fallback : v === "1";
}

export function savePref(name: string, value: boolean) {
  localStorage.setItem(`poker-night:pref:${name}`, value ? "1" : "0");
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; token?: string | null } = {}): Promise<T> {
  const res = await fetch(path, {
    method: init.method ?? "GET",
    headers: {
      "content-type": "application/json",
      ...(init.token ? { "x-player-token": init.token } : {}),
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({ error: "Network error" }));
  if (!res.ok) throw new Error(data.error ?? "Something went wrong");
  return data as T;
}

export const fmt = (n: number) => n.toLocaleString("en-US");
