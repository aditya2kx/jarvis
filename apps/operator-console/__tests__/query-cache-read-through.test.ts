import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const hdrs = { current: new Headers() as Headers | null };
vi.mock("next/headers", () => ({
  headers: async () => {
    if (!hdrs.current) throw new Error("outside a request");
    return hdrs.current;
  },
}));

// One shared store, as every console instance sees it in Firestore.
const store = {
  up: true,
  versions: new Map<string, number>([["t", 1]]),
  entries: new Map<string, string>(),
};
vi.mock("@/lib/bq/cache/firestore", () => ({
  SCHEMA_DOC: "~schema",
  readVersions: async () => (store.up ? new Map(store.versions) : null),
  bumpVersions: vi.fn(async (tables: string[]) => {
    for (const t of tables) store.versions.set(t, (store.versions.get(t) ?? 0) + 1);
  }),
  getEntry: async (k: string) => (store.up ? (store.entries.get(k) ?? null) : null),
  putEntry: async (k: string, json: string) => {
    if (store.up) store.entries.set(k, json);
  },
  storeState: () => ({ namespace: "test", available: store.up, lastError: null }),
}));
vi.mock("@/lib/bq/cache/deps", () => ({
  dependencies: () => ({ known: true, deps: Promise.resolve({ ok: true, objects: ["t", "vw"] }) }),
}));

const { readThrough, cacheStats, noteWrite } = await import("@/lib/bq/cache");
const { bumpVersions } = await import("@/lib/bq/cache/firestore");

let sqlN = 0;
const freshSql = () => `SELECT * FROM \`jarvis-bhaga-prod.bhaga.vw\` WHERE n = ${++sqlN}`;

beforeEach(() => {
  vi.unstubAllEnvs();
  hdrs.current = new Headers();
  store.up = true;
  vi.mocked(bumpVersions).mockClear();
});

describe("readThrough (shared store)", () => {
  it("is on by default; CONSOLE_QUERY_CACHE=off turns it off", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(1);
    expect(cacheStats().disabled).toBe(false);
    vi.stubEnv("CONSOLE_QUERY_CACHE", "off");
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
    expect(cacheStats().disabled).toBe(true);
    expect(cacheStats().recent[0].outcome).toBe("off");
  });

  it("serves the second render from the shared store, as an independent copy", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    const first = await readThrough(sql, undefined, live);
    first[0].a = 99;
    const second = await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(1);
    expect(second).toEqual([{ a: 1 }]);
  });

  it("a version bump from any writer retires the old entry for everyone", async () => {
    const sql = freshSql();
    let value = 1;
    const live = vi.fn(async () => [{ a: value }]);
    expect(await readThrough(sql, undefined, live)).toEqual([{ a: 1 }]);
    value = 2;
    store.versions.set("t", (store.versions.get("t") ?? 0) + 1); // audit-log bump
    expect(await readThrough(sql, undefined, live)).toEqual([{ a: 2 }]);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("a schema bump (view redefined) retires entries too", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    store.versions.set("~schema", (store.versions.get("~schema") ?? 0) + 1);
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("console writes bump the tables they name before returning", async () => {
    const live = vi.fn(async () => []);
    await readThrough("MERGE `jarvis-bhaga-prod.bhaga.store_config` T USING (SELECT 1) S ON FALSE", undefined, live);
    expect(bumpVersions).toHaveBeenCalledWith(["store_config"]);
    await noteWrite("DELETE FROM `jarvis-bhaga-prod.bhaga.t` WHERE TRUE");
    expect(bumpVersions).toHaveBeenLastCalledWith(["t"]);
  });

  it("server actions and reads outside a request always go to BigQuery", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    hdrs.current = new Headers({ "next-action": "abc" });
    await readThrough(sql, undefined, live);
    hdrs.current = null;
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(3);
  });

  it("an unreachable store reads live instead of serving anything", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    store.up = false;
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
    expect(cacheStats().recent[0].outcome).toBe("error");
  });

  it("x-console-cache: off reads live; verify compares cached to live", async () => {
    const sql = freshSql();
    let value = 1;
    const live = vi.fn(async () => [{ a: value }]);
    await readThrough(sql, undefined, live);
    hdrs.current = new Headers({ "x-console-cache": "off" });
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);

    hdrs.current = new Headers({ "x-console-cache": "verify" });
    await readThrough(sql, undefined, live);
    value = 2; // live changed without a version bump: the parity check must flag it
    await readThrough(sql, undefined, live);
    const outcomes = cacheStats().recent.slice(0, 2).map((e) => e.outcome);
    expect(outcomes).toEqual(["verify-diff", "verify-match"]);
  });

  it("does not cache results that would not round-trip through JSON", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: NaN }]);
    await readThrough(sql, undefined, live);
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("concurrent misses on one key share one BigQuery job", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 5));
      return [{ a: 1 }];
    });
    const [x, y] = await Promise.all([readThrough(sql, undefined, live), readThrough(sql, undefined, live)]);
    expect(live).toHaveBeenCalledTimes(1);
    expect(x).toEqual(y);
    expect(x).not.toBe(y);
  });

  it("random SQL is never cached", async () => {
    const rnd = `SELECT RAND() FROM \`jarvis-bhaga-prod.bhaga.t\``;
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(rnd, undefined, live);
    await readThrough(rnd, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
  });
});
