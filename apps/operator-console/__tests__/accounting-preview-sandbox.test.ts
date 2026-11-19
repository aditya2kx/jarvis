import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import { PREVIEW_ACTION_REFUSAL } from "@/lib/config/preview";
import {
  assertPreviewWrite,
  plaidFq,
  plaidSandboxSuffix,
  plaidTable,
  writeTargets,
} from "@/lib/plaid/tables";
import { PREVIEW_SANDBOX_ACTIONS } from "@/lib/actions/registry";

const P = "jarvis-bhaga-prod.bhaga";

function actionPost(path: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    method: "POST",
    headers: { "next-action": "7f3a", "content-type": "text/plain;charset=UTF-8" },
    body: "[]",
  });
}

describe("Accounting preview sandbox (PLAID_SANDBOX_SUFFIX)", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("prod (no CONSOLE_PREVIEW) always uses prod tables, even if the suffix leaks in", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "");
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "_pr999");
    expect(plaidSandboxSuffix()).toBeNull();
    expect(plaidTable("plaid_transactions")).toBe("plaid_transactions");
    expect(plaidFq("plaid_category_rules")).toBe(`\`${P}.plaid_category_rules\``);
    expect(() => assertPreviewWrite(`UPDATE \`${P}.plaid_transactions\` SET x = 1`)).not.toThrow();
  });

  it("a sandboxed preview reads and writes its own copies", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "_pr541");
    expect(plaidFq("plaid_transactions")).toBe(`\`${P}.plaid_transactions_pr541\``);
    expect(() =>
      assertPreviewWrite(`MERGE ${plaidFq("plaid_transactions")} T USING (SELECT 1) S ON FALSE`),
    ).not.toThrow();
  });

  it("refuses any preview write that targets a prod table", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "_pr541");
    for (const sql of [
      `UPDATE \`${P}.plaid_transactions\` SET category_id = NULL`,
      `INSERT INTO \`${P}.plaid_category_rules\` (id) VALUES ('x')`,
      `MERGE \`${P}.store_config\` T USING (SELECT 1) S ON FALSE`,
      `CREATE OR REPLACE TABLE \`${P}.plaid_transactions\` AS SELECT 1`,
      // A script mixing a sandbox and a prod target is refused as a whole.
      `BEGIN TRANSACTION; UPDATE ${plaidFq("plaid_transactions")} SET a = 1; DELETE FROM \`${P}.plaid_items\` WHERE TRUE; COMMIT TRANSACTION;`,
      // Unparseable writes (procedures) are refused rather than guessed at.
      `CALL \`${P}.sp_refresh_order_reco\`('palmetto')`,
    ]) {
      expect(() => assertPreviewWrite(sql), sql).toThrow(/Preview mode/);
    }
  });

  it("a preview without a sandbox refuses every write", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "");
    expect(() => assertPreviewWrite(`UPDATE ${plaidFq("plaid_transactions")} SET a = 1`)).toThrow();
  });

  it("rejects a malformed suffix (cannot be used to name a prod table)", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "` OR 1=1");
    expect(plaidSandboxSuffix()).toBeNull();
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "");
    expect(plaidSandboxSuffix()).toBeNull();
  });

  it("finds every write target in a transaction script", () => {
    expect(
      writeTargets(
        `BEGIN TRANSACTION; INSERT INTO \`a.b.r\` (id) VALUES (1); INSERT INTO \`a.b.e\` SELECT 1; UPDATE \`a.b.t\` SET x=1; COMMIT;`,
      ),
    ).toEqual(["a.b.r", "a.b.e", "a.b.t"]);
    expect(writeTargets("SELECT * FROM `a.b.t`")).toEqual([]);
  });

  it("middleware lets Accounting actions through only on a sandboxed preview", () => {
    vi.stubEnv("CONSOLE_PREVIEW", "1");
    expect(middleware(actionPost("/accounting")).status).toBe(403);
    vi.stubEnv("PLAID_SANDBOX_SUFFIX", "_pr541");
    expect(middleware(actionPost("/accounting")).status).toBe(200);
    expect(middleware(actionPost("/labor")).status).toBe(403);
    expect(middleware(actionPost("/home")).status).toBe(403);
    // Not a server action (no next-action header) → still refused.
    const plain = new NextRequest("http://localhost:3000/accounting", { method: "POST" });
    expect(middleware(plain).status).toBe(403);
  });

  it("bank link / sync are never preview-sandbox actions", () => {
    for (const name of ["createPlaidLinkTokenAction", "exchangePlaidPublicTokenAction", "syncPlaidNowAction"]) {
      expect(PREVIEW_SANDBOX_ACTIONS).not.toContain(name);
    }
    expect(PREVIEW_ACTION_REFUSAL).toMatch(/Nothing was saved/);
  });
});
