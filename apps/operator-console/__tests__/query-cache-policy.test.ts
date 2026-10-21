import { readdirSync, readFileSync } from "fs";
import path from "path";
import { describe, expect, it } from "vitest";
import { BigQuery } from "@google-cloud/bigquery";
import {
  cacheKey,
  isExactJson,
  isReadOnlySql,
  namedObjects,
  paramsFingerprint,
  sameRows,
  timeBucket,
  volatility,
} from "@/lib/bq/cache/policy";

describe("isReadOnlySql", () => {
  it("accepts SELECT / WITH, including after comments", () => {
    expect(isReadOnlySql("SELECT 1")).toBe(true);
    expect(isReadOnlySql("  -- note\n  WITH a AS (SELECT 1) SELECT * FROM a")).toBe(true);
    expect(isReadOnlySql("/* x */ (SELECT 1)")).toBe(true);
  });
  it("treats everything else as a write", () => {
    for (const s of ["MERGE t USING s ON", "INSERT INTO t", "DELETE FROM t", "UPDATE t SET", "CALL sp()", "BEGIN"]) {
      expect(isReadOnlySql(s)).toBe(false);
    }
  });
});

describe("volatility + timeBucket", () => {
  it("dates are covered by the hour bucket; clock reads by the minute; random never cached", () => {
    expect(volatility("SELECT CURRENT_DATE('America/Chicago')")).toBe("stable");
    expect(volatility("SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), t, MINUTE)")).toBe("minute");
    expect(volatility("SELECT RAND()")).toBe("never");
    expect(volatility("SELECT GENERATE_UUID()")).toBe("never");
    const now = new Date("2026-10-21T05:59:30Z");
    expect(timeBucket(now, "stable")).toBe("2026-10-21T05");
    expect(timeBucket(now, "minute")).toBe("2026-10-21T05:59");
  });
});

describe("cacheKey", () => {
  const base = { sql: "SELECT 1", params: "", versions: { a: 1, b: 2 }, schemaVersion: 9, bucket: "h" };
  it("changes when any read table changes, the schema version, or the hour", () => {
    const k = cacheKey(base);
    expect(cacheKey({ ...base, versions: { b: 2, a: 1 } })).toBe(k);
    expect(cacheKey({ ...base, versions: { a: 1, b: 3 } })).not.toBe(k);
    expect(cacheKey({ ...base, schemaVersion: 10 })).not.toBe(k);
    expect(cacheKey({ ...base, bucket: "h2" })).not.toBe(k);
  });
  it("distinguishes DATE vs STRING params with the same text", () => {
    expect(paramsFingerprint({ d: BigQuery.date("2026-10-01") })).not.toBe(paramsFingerprint({ d: "2026-10-01" }));
    expect(paramsFingerprint({ b: 1, a: 2 })).toBe(paramsFingerprint({ a: 2, b: 1 }));
  });
});

describe("isExactJson", () => {
  it("only plain JSON round-trips exactly", () => {
    expect(isExactJson([{ a: 1, b: "x", c: null, d: [true] }])).toBe(true);
    expect(isExactJson([{ a: NaN }])).toBe(false);
    expect(isExactJson([{ a: undefined }])).toBe(false);
    expect(isExactJson([{ a: new Date() }])).toBe(false);
    expect(isExactJson([{ a: BigInt(1) }])).toBe(false);
  });
});

describe("sameRows", () => {
  it("compares row multisets and reports order", () => {
    expect(sameRows([{ a: 1 }, { a: 2 }], [{ a: 1 }, { a: 2 }])).toEqual({ equal: true, sameOrder: true });
    expect(sameRows([{ a: 1 }, { a: 2 }], [{ a: 2 }, { a: 1 }])).toEqual({ equal: true, sameOrder: false });
    expect(sameRows([{ a: 1 }], [{ a: 1.01 }]).equal).toBe(false);
  });
});

describe("namedObjects", () => {
  it("finds fq() and literal references", () => {
    const sql =
      "SELECT * FROM `jarvis-bhaga-prod.bhaga.vw_a` JOIN `jarvis-bhaga-prod`.`bhaga`.`t_b` USING (x) JOIN bhaga.c";
    expect(namedObjects(sql, "jarvis-bhaga-prod", "bhaga").sort()).toEqual(["c", "t_b", "vw_a"]);
  });
});

// Keys carry the UTC hour, which covers CURRENT_DATE in any whole-hour zone. A view or
// table function reading the clock (or random) would make cached results wrong within
// the hour — such a definition needs a policy change in lib/bq/cache/policy.ts first.
describe("migrations keep views cache-safe", () => {
  it("no view or table function reads the clock beyond CURRENT_DATE", () => {
    const dir = path.resolve(__dirname, "../../../core/migrations");
    const offenders: string[] = [];
    for (const f of readdirSync(dir).filter((n) => n.endsWith(".sql"))) {
      const text = readFileSync(path.join(dir, f), "utf8").replace(/--[^\n]*/g, "");
      for (const stmt of text.split(/;\s*\n/)) {
        if (!/CREATE\s+(?:OR\s+REPLACE\s+)?(?:VIEW|TABLE\s+FUNCTION)\b/i.test(stmt)) continue;
        if (volatility(stmt) !== "stable") offenders.push(`${f}: ${stmt.trim().slice(0, 80)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
