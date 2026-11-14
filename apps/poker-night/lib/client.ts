"use client";

import type { Room } from "./game";

export type RoomResponse = { room?: Room; version: number; you: string | null; unchanged?: boolean; error?: string };

const key = (roomId: string) => `poker-night:${roomId}`;

export function savedToken(roomId: string) {
  return typeof window === "undefined" ? null : localStorage.getItem(key(roomId));
}

export function saveToken(roomId: string, token: string) {
  localStorage.setItem(key(roomId), token);
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
