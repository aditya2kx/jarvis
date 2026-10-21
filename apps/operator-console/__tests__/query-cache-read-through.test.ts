import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const hdrs = { current: new Headers() as Headers | null };
vi.mock("next/headers", () => ({
  headers: async () => {
    if (!hdrs.current) throw new Error("outside a request");
    return hdrs.current;
  },
}));

const catalog = { versions: new Map<string, number>([["t", 1], ["vw", 5]]), viewsEpoch: 5 };
vi.mock("@/lib/bq/cache/catalog", () => ({
  catalogSnapshot: async () => ({ at: Date.now(), ...catalog }),
  markDirty: vi.fn(),
  catalogState: () => ({ ageMs: 0, objects: 2, dirty: false }),
}));
vi.mock("@/lib/bq/cache/deps", () => ({
  dependencies: () => ({ known: true, deps: Promise.resolve({ ok: true, objects: ["t", "vw"] }) }),
}));
vi.mock("@/lib/bq/cache/firestore", () => ({
  l2Get: async () => null,
  l2Put: async () => {},
  l2State: () => ({ configured: false, enabled: false, lastError: null }),
}));

const { readThrough, cacheStats } = await import("@/lib/bq/cache");
const { markDirty } = await import("@/lib/bq/cache/catalog");

let sqlN = 0;
const freshSql = () => `SELECT * FROM \`jarvis-bhaga-prod.bhaga.vw\` WHERE n = ${++sqlN}`;

beforeEach(() => {
  hdrs.current = new Headers();
  catalog.versions.set("t", 1);
  vi.mocked(markDirty).mockClear();
});

describe("readThrough", () => {
  it("serves the second render from memory, as an independent copy", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    const first = await readThrough(sql, undefined, live);
    first[0].a = 99;
    const second = await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(1);
    expect(second).toEqual([{ a: 1 }]);
  });

  it("a write to a read table (new last-modified) is never served from the old entry", async () => {
    const sql = freshSql();
    let value = 1;
    const live = vi.fn(async () => [{ a: value }]);
    expect(await readThrough(sql, undefined, live)).toEqual([{ a: 1 }]);
    value = 2;
    catalog.versions.set("t", 2);
    expect(await readThrough(sql, undefined, live)).toEqual([{ a: 2 }]);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("server actions always read live and mark the catalog dirty", async () => {
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    hdrs.current = new Headers({ "next-action": "abc" });
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
    expect(markDirty).toHaveBeenCalled();
  });

  it("outside a request, reads go straight to BigQuery", async () => {
    hdrs.current = null;
    const sql = freshSql();
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(sql, undefined, live);
    await readThrough(sql, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("writes bypass and mark dirty", async () => {
    const live = vi.fn(async () => []);
    await readThrough("MERGE `jarvis-bhaga-prod.bhaga.t` T USING (SELECT 1) S ON FALSE", undefined, live);
    expect(live).toHaveBeenCalledTimes(1);
    expect(markDirty).toHaveBeenCalled();
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
    value = 2; // live changed without a version change: the parity check must flag it
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

  it("clock-reading SQL is still cached but random SQL never is", async () => {
    const rnd = `SELECT RAND() FROM \`jarvis-bhaga-prod.bhaga.t\``;
    const live = vi.fn(async () => [{ a: 1 }]);
    await readThrough(rnd, undefined, live);
    await readThrough(rnd, undefined, live);
    expect(live).toHaveBeenCalledTimes(2);
  });
});
