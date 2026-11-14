import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(__dirname, "..");
const SKIP = new Set(["node_modules", ".next"]);
const SOURCE = /\.(ts|tsx|mjs|css)$/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    if (SKIP.has(name)) return [];
    const full = join(dir, name);
    return statSync(full).isDirectory() ? files(full) : SOURCE.test(name) ? [full] : [];
  });
}

describe("poker-night stays separate from the rest of the repo", () => {
  const sources = files(ROOT).filter((f) => !f.endsWith("isolation.test.ts"));

  it("never imports from outside apps/poker-night", () => {
    const offenders: string[] = [];
    for (const file of sources) {
      for (const [, spec] of readFileSync(file, "utf8").matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)) {
        if (!spec.startsWith(".")) continue;
        const target = resolve(file, "..", spec);
        if (relative(ROOT, target).startsWith("..")) offenders.push(`${relative(ROOT, file)} → ${spec}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("does not reference Palmetto / Operator Console / agent code", () => {
    const pattern = /operator-console|palmetto|bhaga|chitra|akshaya|chanakya|agents\/|skills\//i;
    const offenders = sources.filter((f) => pattern.test(readFileSync(f, "utf8"))).map((f) => relative(ROOT, f));
    expect(offenders).toEqual([]);
  });
});
