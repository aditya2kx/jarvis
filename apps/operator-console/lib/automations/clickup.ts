import "server-only";

import {
  DEFAULT_WORKSPACE_ID,
  type ClickUpNamedOption,
} from "@/lib/automations/clickupTypes";

export { DEFAULT_WORKSPACE_ID, type ClickUpNamedOption };

const API_BASE = "https://api.clickup.com";

function getPat(): string {
  const pat = (process.env.CLICKUP_PAT ?? "").trim();
  if (!pat) {
    throw new Error(
      "CLICKUP_PAT not set. For localhost: add CLICKUP_PAT=pk_… to apps/operator-console/.env.local " +
        "(Keychain service jarvis-clickup-palmetto-pat). On Cloud Run, mount secret jarvis-clickup-palmetto-pat.",
    );
  }
  return pat;
}

async function clickupFetch(
  path: string,
  init: RequestInit & { method?: string } = {},
): Promise<unknown> {
  const pat = getPat();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: pat,
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  const text = await res.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!res.ok) {
    throw new Error(
      `ClickUp ${init.method ?? "GET"} ${path} → ${res.status}: ${text.slice(0, 400)}`,
    );
  }
  return data;
}

/** Named public CHANNEL chats only (skip DMs / unnamed). */
export async function listNamedChannels(
  workspaceId = DEFAULT_WORKSPACE_ID,
): Promise<ClickUpNamedOption[]> {
  const out: ClickUpNamedOption[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 20; page++) {
    const qs = new URLSearchParams({ limit: "100" });
    if (cursor) qs.set("cursor", cursor);
    const data = (await clickupFetch(
      `/api/v3/workspaces/${workspaceId}/chat/channels?${qs}`,
    )) as { data?: Record<string, unknown>[]; next_cursor?: string };
    for (const c of data.data ?? []) {
      const name = String(c.name ?? "").trim();
      const type = String(c.type ?? "");
      const id = String(c.id ?? "");
      if (!id || !name) continue;
      if (type && type !== "CHANNEL") continue;
      out.push({ id, label: name.startsWith("#") ? name : `#${name}` });
    }
    cursor = data.next_cursor ?? null;
    if (!cursor) break;
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

/** Workspace members for DM picker (display name). */
export async function listWorkspaceMembers(
  workspaceId = DEFAULT_WORKSPACE_ID,
): Promise<ClickUpNamedOption[]> {
  const data = (await clickupFetch("/api/v2/team")) as {
    teams?: {
      id: string | number;
      members?: { user?: Record<string, unknown> }[];
    }[];
  };
  const team = (data.teams ?? []).find(
    (t) => String(t.id) === String(workspaceId),
  );
  const out: ClickUpNamedOption[] = [];
  for (const m of team?.members ?? []) {
    const u = m.user ?? {};
    const id = String(u.id ?? "");
    if (!id) continue;
    const label =
      String(u.username ?? "").trim() ||
      String(u.email ?? "").trim() ||
      id;
    out.push({ id, label });
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

export async function ensureDmChannel(
  userIds: string[],
  workspaceId = DEFAULT_WORKSPACE_ID,
): Promise<{ id: string }> {
  const data = (await clickupFetch(
    `/api/v3/workspaces/${workspaceId}/chat/channels/direct_message`,
    {
      method: "POST",
      body: JSON.stringify({ user_ids: userIds.map(String) }),
    },
  )) as { data?: { id?: string }; id?: string };
  const channel = data.data ?? data;
  const id = channel.id;
  if (!id) {
    throw new Error(`ClickUp DM create returned no id: ${JSON.stringify(data)}`);
  }
  return { id };
}

export async function postChatMessage(
  channelId: string,
  content: string,
  workspaceId = DEFAULT_WORKSPACE_ID,
): Promise<{ id: string }> {
  const data = (await clickupFetch(
    `/api/v3/workspaces/${workspaceId}/chat/channels/${channelId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({
        type: "message",
        content,
        content_format: "text/md",
      }),
    },
  )) as { data?: { id?: string }; id?: string };
  const msg = data.data ?? data;
  const id = msg.id;
  if (!id) {
    throw new Error(
      `ClickUp post returned no message id: ${JSON.stringify(data)}`,
    );
  }
  return { id };
}
