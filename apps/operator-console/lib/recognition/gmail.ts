import "server-only";

import { readSecret } from "@/lib/recognition/secrets";

// Gmail send for gift-card emails (Issue #369). Sender grant (gmail.send only)
// lives in Secret Manager `gmail_sender_palmetto`, created by
// scripts/gmail_sender_grant.py.

const SECRET_ID = "gmail_sender_palmetto";

type SenderSecret = { client_id: string; client_secret: string; refresh_token: string; email: string };

async function sender(): Promise<SenderSecret & { accessToken: string }> {
  const s = JSON.parse(await readSecret(SECRET_ID)) as SenderSecret;
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: s.client_id,
      client_secret: s.client_secret,
      refresh_token: s.refresh_token,
      grant_type: "refresh_token",
    }),
  });
  const body = (await res.json()) as { access_token?: string; error?: string };
  if (!res.ok || !body.access_token) {
    throw new Error(
      `Gmail token refresh failed (${body.error ?? res.status}) — re-run apps/operator-console/scripts/gmail_sender_grant.py`,
    );
  }
  return { ...s, accessToken: body.access_token };
}

export async function senderEmail(): Promise<string | null> {
  try {
    return (JSON.parse(await readSecret(SECRET_ID)) as SenderSecret).email;
  } catch {
    return null;
  }
}

function encodeHeader(value: string): string {
  return /^[\x20-\x7e]*$/.test(value)
    ? value
    : `=?UTF-8?B?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

/** Build an RFC 2822 multipart/alternative message (base64url) — exported for tests. */
export function buildMime(input: {
  from: string;
  to: string;
  cc?: string;
  subject: string;
  text: string;
  html: string;
}): string {
  const boundary = `rec-${Math.random().toString(36).slice(2)}`;
  const lines = [
    `From: Palmetto Superfoods Austin <${input.from}>`,
    `To: ${input.to}`,
    ...(input.cc ? [`Cc: ${input.cc}`] : []),
    `Subject: ${encodeHeader(input.subject)}`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    Buffer.from(input.text, "utf8").toString("base64"),
    `--${boundary}`,
    'Content-Type: text/html; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    Buffer.from(input.html, "utf8").toString("base64"),
    `--${boundary}--`,
    "",
  ];
  return Buffer.from(lines.join("\r\n"), "utf8").toString("base64url");
}

export async function sendEmail(input: {
  to: string;
  cc?: string;
  subject: string;
  text: string;
  html: string;
}): Promise<{ id: string; from: string }> {
  const s = await sender();
  const res = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
    method: "POST",
    headers: { Authorization: `Bearer ${s.accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ raw: buildMime({ ...input, from: s.email }) }),
  });
  const body = (await res.json()) as { id?: string; error?: { message?: string } };
  if (!res.ok || !body.id) {
    throw new Error(`Gmail send failed (${res.status}): ${body.error?.message ?? "no id"}`);
  }
  return { id: body.id, from: s.email };
}
