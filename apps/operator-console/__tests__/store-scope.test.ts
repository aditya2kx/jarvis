import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const cookieStore = { value: undefined as string | undefined };
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => (cookieStore.value === undefined ? undefined : { value: cookieStore.value }),
  }),
}));

const getAccessToken = vi.fn();
vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    getClient = async () => ({ getAccessToken });
  },
}));

import { forStore, storeDataset } from "@/lib/bq/bigquery";
import { currentStore } from "@/lib/config/current-store";
import { parseStore } from "@/lib/config/stores";
import { triggerAdpSync, withStoreEnv } from "@/lib/bhaga/recompute";

const T = (name: string, dataset = "bhaga") => "`jarvis-bhaga-prod." + dataset + "." + name + "`";

describe("parseStore", () => {
  it("keeps a known store and falls back to Austin for anything else", () => {
    expect(parseStore("houston")).toBe("houston");
    expect(parseStore("palmetto")).toBe("palmetto");
    expect(parseStore("dallas")).toBe("palmetto");
    expect(parseStore(undefined)).toBe("palmetto");
    expect(parseStore("")).toBe("palmetto");
  });
});

describe("currentStore", () => {
  it("reads the store cookie, defaulting to Austin", async () => {
    cookieStore.value = undefined;
    expect(await currentStore()).toBe("palmetto");
    cookieStore.value = "houston";
    expect(await currentStore()).toBe("houston");
    cookieStore.value = "'; DROP TABLE x; --";
    expect(await currentStore()).toBe("palmetto");
  });
});

describe("forStore", () => {
  const sql = `SELECT * FROM ${T("orders")} o JOIN ${T("vw_labor_daily")} l USING (d) WHERE o.store = @store`;

  it("leaves Austin statements byte-identical", () => {
    expect(forStore(sql, "palmetto")).toBe(sql);
  });

  it("points every table at the Houston dataset", () => {
    expect(storeDataset("houston")).toBe("bhaga_houston");
    expect(forStore(sql, "houston")).toBe(
      `SELECT * FROM ${T("orders", "bhaga_houston")} o JOIN ${T("vw_labor_daily", "bhaga_houston")} l USING (d) WHERE o.store = @store`,
    );
  });

  it("keeps console-wide tables in the default dataset", () => {
    const grants = `SELECT * FROM ${T("console_access_grants")} g, ${T("goals")} x`;
    expect(forStore(grants, "houston")).toBe(
      `SELECT * FROM ${T("console_access_grants")} g, ${T("goals", "bhaga_houston")} x`,
    );
  });

  it("does not touch string literals or other datasets", () => {
    const other = "SELECT 'jarvis-bhaga-prod.bhaga.orders' AS s FROM `jarvis-bhaga-prod.jarvis_dev.t`";
    expect(forStore(other, "houston")).toBe(other);
  });
});

describe("store-scoped job runs", () => {
  beforeEach(() => {
    getAccessToken.mockReset();
    getAccessToken.mockResolvedValue({ token: "t" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, text: async () => "", json: async () => ({}) }),
    );
  });

  const runEnv = () => {
    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith(":run"));
    const body = JSON.parse(String((call![1] as RequestInit).body));
    return Object.fromEntries(
      (body.overrides.containerOverrides[0].env as { name: string; value: string }[]).map((e) => [e.name, e.value]),
    );
  };

  it("sends Austin runs without dataset or collection overrides", async () => {
    const env = [{ name: "REFRESH_DATE", value: "2026-11-20" }];
    expect(withStoreEnv(env, "palmetto")).toBe(env);
  });

  it("overrides store, dataset and collection together for Houston", () => {
    const env = withStoreEnv(
      [
        { name: "REFRESH_DATE", value: "2026-11-23" },
        { name: "BHAGA_STORE", value: "palmetto" },
      ],
      "houston",
    );
    expect(env).toEqual([
      { name: "REFRESH_DATE", value: "2026-11-23" },
      { name: "BHAGA_STORE", value: "houston" },
      { name: "BHAGA_BQ_DATASET", value: "bhaga_houston" },
      { name: "BHAGA_FIRESTORE_COLLECTION", value: "runs_houston" },
    ]);
  });

  it("a job started while Houston is selected runs against Houston", async () => {
    cookieStore.value = "houston";
    await triggerAdpSync("houston", "2026-11-23");
    expect(runEnv()).toMatchObject({
      BHAGA_STORE: "houston",
      BHAGA_BQ_DATASET: "bhaga_houston",
      BHAGA_FIRESTORE_COLLECTION: "runs_houston",
    });
  });

  it("a job started while Austin is selected carries no Houston override", async () => {
    cookieStore.value = "palmetto";
    await triggerAdpSync("palmetto", "2026-11-20");
    const env = runEnv();
    expect(env.BHAGA_STORE).toBe("palmetto");
    expect(env.BHAGA_BQ_DATASET).toBeUndefined();
    expect(env.BHAGA_FIRESTORE_COLLECTION).toBeUndefined();
  });
});
