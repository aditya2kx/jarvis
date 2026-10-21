import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { normalizeGrant, type GrantVersion } from "@/lib/access/policy";

vi.mock("server-only", () => ({}));
vi.mock("react", async (orig) => ({ ...(await orig<typeof import("react")>()), cache: <T,>(fn: T) => fn }));

const state = vi.hoisted(() => ({
  email: "am@example.com",
  viewAs: undefined as string | undefined,
  latest: new Map<string, unknown>(),
  storeFails: false,
  cookieSet: vi.fn(),
  cookieDelete: vi.fn(),
  appendGrant: vi.fn(),
  setIapAccess: vi.fn(),
}));

vi.mock("@/lib/auth/identity", () => ({ operatorEmail: async () => state.email }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => (state.viewAs ? { value: state.viewAs } : undefined),
    set: state.cookieSet,
    delete: state.cookieDelete,
  }),
}));
vi.mock("@/lib/access/store", () => ({
  latestGrants: async () => {
    if (state.storeFails) throw new Error("bq down");
    return state.latest;
  },
  appendGrant: state.appendGrant,
  grantHistory: async () => [],
}));
vi.mock("@/lib/access/iap", () => ({ setIapAccess: state.setIapAccess, iapStatus: async () => ({ ok: true, members: [] }) }));

import { actionDenied, getAccess } from "@/lib/access/server";
import { POST } from "@/app/api/access/route";

const grantFor = (email: string, pages: object, status: "active" | "revoked" = "active"): GrantVersion => ({
  email,
  version: 1,
  status,
  grant: normalizeGrant({ pages }),
  note: null,
  createdBy: "adi@mypalmetto.co",
  createdAt: "2026-10-21T00:00:00Z",
});

const post = (body: object) =>
  POST(new NextRequest("http://localhost/api/access", { method: "POST", body: JSON.stringify(body) }));

beforeEach(() => {
  vi.stubEnv("CONSOLE_OWNER_EMAILS", "");
  vi.stubEnv("CONSOLE_ACCESS_DEFAULT", "");
  vi.stubEnv("BYPASS_IAP_EMAIL", "");
  state.email = "am@example.com";
  state.viewAs = undefined;
  state.latest = new Map();
  state.storeFails = false;
  state.appendGrant.mockReset();
  state.setIapAccess.mockReset().mockResolvedValue("granted");
  state.cookieSet.mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe("getAccess", () => {
  it("fails closed when the grants store can't be read", async () => {
    state.storeFails = true;
    const a = await getAccess();
    expect(a.grant).toEqual({ full: false, pages: {} });
    expect(a.error).toMatch(/Couldn't check your access/);
  });

  it("owners skip the store entirely", async () => {
    state.email = "adi@mypalmetto.co";
    state.storeFails = true;
    const a = await getAccess();
    expect(a).toMatchObject({ isOwner: true, realIsOwner: true, error: null });
  });

  it("View as is honored for owners only", async () => {
    state.latest.set("am@example.com", grantFor("am@example.com", { labor: ["coverage"] }));
    state.email = "adi@mypalmetto.co";
    state.viewAs = "am@example.com";
    const owner = await getAccess();
    expect(owner).toMatchObject({ email: "am@example.com", viewingAs: "am@example.com", realIsOwner: true, isOwner: false });
    expect(owner.grant.pages).toEqual({ labor: ["coverage"] });

    state.email = "other@example.com";
    const other = await getAccess();
    expect(other).toMatchObject({ email: "other@example.com", viewingAs: null });
  });
});

describe("actionDenied", () => {
  it("lets an action through when any one access key is held", async () => {
    state.latest.set("am@example.com", grantFor("am@example.com", { labor: ["labor-hours"] }));
    expect(await actionDenied("saveGoalAction")).toBeNull();
    expect(await actionDenied("saveGoalsAction")).toMatchObject({ ok: false });
  });

  it("refuses a revoked person and an unreadable store", async () => {
    state.latest.set("am@example.com", grantFor("am@example.com", {}, "revoked"));
    expect(await actionDenied("syncAdpAction")).toMatchObject({ ok: false });
    state.storeFails = true;
    expect(await actionDenied("syncAdpAction")).toMatchObject({ ok: false, error: expect.stringMatching(/Nothing was changed/) });
  });

  it("keeps today's behavior for people with nothing saved (default full)", async () => {
    expect(await actionDenied("runPayrollDraftAction")).toBeNull();
    vi.stubEnv("CONSOLE_ACCESS_DEFAULT", "none");
    expect(await actionDenied("runPayrollDraftAction")).toMatchObject({ ok: false });
  });
});

describe("POST /api/access", () => {
  it("is owner-only", async () => {
    const res = await post({ op: "save", email: "x@example.com", grant: { full: true } });
    expect(res.status).toBe(403);
    expect(state.appendGrant).not.toHaveBeenCalled();
  });

  it("saves a new version and grants Google sign-in", async () => {
    state.email = "adi@mypalmetto.co";
    const res = await post({ op: "save", email: " AM2@Example.com ", grant: { pages: { labor: ["coverage"] } }, note: "AM" });
    const body = await res.json();
    expect(body).toMatchObject({ ok: true, message: expect.stringMatching(/Saved access for am2@example.com/) });
    expect(state.appendGrant).toHaveBeenCalledWith({
      email: "am2@example.com",
      status: "active",
      grant: { full: false, pages: { labor: ["coverage"] } },
      note: "AM",
      by: "adi@mypalmetto.co",
    });
    expect(state.setIapAccess).toHaveBeenCalledWith("am2@example.com", true);
  });

  it("a grant with no pages is a removal, and sign-in goes too", async () => {
    state.email = "adi@mypalmetto.co";
    state.setIapAccess.mockResolvedValue("removed");
    await post({ op: "save", email: "lindsay@example.com", grant: { pages: {} } });
    expect(state.appendGrant).toHaveBeenCalledWith(expect.objectContaining({ status: "revoked" }));
    expect(state.setIapAccess).toHaveBeenCalledWith("lindsay@example.com", false);
  });

  it("still saves when Google sign-in can't be changed, and says so", async () => {
    state.email = "adi@mypalmetto.co";
    state.setIapAccess.mockRejectedValue(new Error("403"));
    const body = await (await post({ op: "revoke", email: "lindsay@example.com" })).json();
    expect(state.appendGrant).toHaveBeenCalled();
    expect(body).toMatchObject({ ok: true, message: expect.stringMatching(/Google sign-in couldn't be removed/) });
  });

  it("refuses to edit an owner or a bad email", async () => {
    state.email = "adi@mypalmetto.co";
    expect((await post({ op: "revoke", email: "aditya.2ky@gmail.com" })).status).toBe(400);
    expect((await post({ op: "save", email: "nope", grant: {} })).status).toBe(400);
    expect(state.appendGrant).not.toHaveBeenCalled();
  });

  it("View as sets a cookie for owners", async () => {
    state.email = "adi@mypalmetto.co";
    await post({ op: "view-as", email: "AM@example.com" });
    expect(state.cookieSet).toHaveBeenCalledWith("oc_view_as", "am@example.com", expect.objectContaining({ httpOnly: true }));
  });
});
