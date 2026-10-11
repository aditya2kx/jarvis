import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import { PREVIEW_ACTION_REFUSAL } from "@/lib/config/preview";

const URL_ = "http://localhost:3000/labor";

function actionPost() {
  return new NextRequest(URL_, {
    method: "POST",
    headers: { "next-action": "7f3a", "content-type": "text/plain;charset=UTF-8" },
    body: "[]",
  });
}

describe("middleware read-only preview gate (CONSOLE_PREVIEW=1)", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("refuses server-action POSTs with a text/plain 403 the action toast can show", async () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    const res = middleware(actionPost());
    expect(res.status).toBe(403);
    expect(res.headers.get("content-type")).toBe("text/plain");
    expect(await res.text()).toBe(PREVIEW_ACTION_REFUSAL);
  });

  it("lets page GETs through so preview still renders", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    const res = middleware(new NextRequest(URL_));
    expect(res.status).toBe(200);
  });

  it("lets non-action POSTs through (e.g. /api/iap-beacon)", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    const res = middleware(new NextRequest("http://localhost:3000/api/iap-beacon", { method: "POST" }));
    expect(res.status).toBe(200);
  });

  it("does nothing when CONSOLE_PREVIEW is unset (prod)", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "");
    const res = middleware(actionPost());
    expect(res.status).toBe(200);
  });
});
