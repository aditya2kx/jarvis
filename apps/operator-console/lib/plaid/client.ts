import "server-only";

/**
 * Thin Plaid REST client for Operator Console (Issue #158).
 * Mirrors skills/plaid_api/client.py — urllib → fetch, no SDK.
 */

const HOSTS: Record<string, string> = {
  sandbox: "https://sandbox.plaid.com",
  development: "https://development.plaid.com",
  production: "https://production.plaid.com",
};

function plaidEnv(): string {
  return (process.env.PLAID_ENV || "sandbox").trim().toLowerCase();
}

function apiBase(): string {
  const env = plaidEnv();
  const base = HOSTS[env];
  if (!base) throw new Error(`Unknown PLAID_ENV=${env}`);
  return base;
}

function credentials(): { client_id: string; secret: string } {
  const client_id = process.env.PLAID_CLIENT_ID?.trim();
  const secret = process.env.PLAID_SECRET?.trim();
  if (!client_id || !secret) {
    throw new Error(
      "PLAID_CLIENT_ID / PLAID_SECRET missing — mount from Secret Manager on operator-console Cloud Run",
    );
  }
  return { client_id, secret };
}

async function plaidPost(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const creds = credentials();
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Plaid-Version": "2020-09-14",
    },
    body: JSON.stringify({ ...creds, ...body }),
  });
  const text = await res.text();
  let json: Record<string, unknown> = {};
  try {
    json = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    throw new Error(`Plaid ${path} non-JSON ${res.status}: ${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    throw new Error(`Plaid ${path} failed ${res.status}: ${text.slice(0, 400)}`);
  }
  return json;
}

export async function createLinkToken(
  clientUserId: string,
  webhookUrl?: string,
  redirectUri?: string,
): Promise<string> {
  const body: Record<string, unknown> = {
    user: { client_user_id: clientUserId },
    client_name: "Palmetto Operator Console",
    products: ["transactions"],
    country_codes: ["US"],
    language: "en",
    transactions: { days_requested: 730 },
  };
  if (webhookUrl) body.webhook = webhookUrl;
  // Required for Chase (and other OAuth banks) so the bank can return to Link.
  if (redirectUri) body.redirect_uri = redirectUri;
  const data = await plaidPost("/link/token/create", body);
  const token = data.link_token;
  if (typeof token !== "string") throw new Error("Plaid link_token missing");
  return token;
}

export async function exchangePublicToken(publicToken: string): Promise<{
  access_token: string;
  item_id: string;
}> {
  const data = await plaidPost("/item/public_token/exchange", { public_token: publicToken });
  const access_token = data.access_token;
  const item_id = data.item_id;
  if (typeof access_token !== "string" || typeof item_id !== "string") {
    throw new Error("Plaid exchange missing access_token/item_id");
  }
  return { access_token, item_id };
}

/** Best-effort institution display name for plaid_items (null if lookup fails). */
export async function fetchInstitutionName(accessToken: string): Promise<string | null> {
  try {
    const itemData = await plaidPost("/item/get", { access_token: accessToken });
    const item = (itemData.item || {}) as Record<string, unknown>;
    const institutionId = item.institution_id;
    if (typeof institutionId !== "string" || !institutionId) return null;
    const inst = await plaidPost("/institutions/get_by_id", {
      institution_id: institutionId,
      country_codes: ["US"],
    });
    const name = (inst.institution as Record<string, unknown> | undefined)?.name;
    return typeof name === "string" ? name : null;
  } catch {
    return null;
  }
}

export interface PlaidSyncPage {
  added: Record<string, unknown>[];
  modified: Record<string, unknown>[];
  removed: { transaction_id?: string }[];
  next_cursor: string;
  has_more: boolean;
}

export async function transactionsSync(
  accessToken: string,
  cursor: string | null,
): Promise<PlaidSyncPage> {
  const body: Record<string, unknown> = {
    access_token: accessToken,
    count: 500,
    options: { include_personal_finance_category: true },
  };
  if (cursor != null) body.cursor = cursor;
  const data = await plaidPost("/transactions/sync", body);
  return {
    added: (data.added as Record<string, unknown>[]) || [],
    modified: (data.modified as Record<string, unknown>[]) || [],
    removed: (data.removed as { transaction_id?: string }[]) || [],
    next_cursor: String(data.next_cursor || cursor || ""),
    has_more: Boolean(data.has_more),
  };
}

export interface PlaidAccountInfo {
  account_id: string;
  name: string | null;
  mask: string | null;
  type: string | null;
  subtype: string | null;
}

export async function fetchAccounts(accessToken: string): Promise<PlaidAccountInfo[]> {
  const data = await plaidPost("/accounts/get", { access_token: accessToken });
  const accounts = (data.accounts as Record<string, unknown>[]) || [];
  return accounts
    .filter((a) => typeof a.account_id === "string")
    .map((a) => ({
      account_id: String(a.account_id),
      name: a.name != null ? String(a.name) : null,
      mask: a.mask != null ? String(a.mask) : null,
      type: a.type != null ? String(a.type) : null,
      subtype: a.subtype != null ? String(a.subtype) : null,
    }));
}

export function accessTokenSecretId(itemId: string): string {
  const safe = itemId.replace(/[^a-zA-Z0-9_-]/g, "_");
  return `plaid_access_token_${safe}`;
}

export async function saveAccessTokenSecret(itemId: string, accessToken: string): Promise<void> {
  const { SecretManagerServiceClient } = await import("@google-cloud/secret-manager");
  const client = new SecretManagerServiceClient();
  const project = process.env.GCP_PROJECT || process.env.BQ_PROJECT || "jarvis-bhaga-prod";
  const secretId = accessTokenSecretId(itemId);
  const parent = `projects/${project}`;
  const name = `${parent}/secrets/${secretId}`;
  let created = false;
  try {
    await client.getSecret({ name });
  } catch {
    await client.createSecret({
      parent,
      secretId,
      secret: { replication: { automatic: {} } },
    });
    created = true;
  }
  await client.addSecretVersion({
    parent: name,
    payload: { data: Buffer.from(accessToken, "utf8") },
  });
  // Newly created secrets need an explicit secretAccessor binding for the
  // console + webhook runtime SAs (project Editor alone is not enough for
  // versions.access on secrets created at runtime — Sync now 403'd otherwise).
  if (created) {
    const consoleSa =
      process.env.OPERATOR_CONSOLE_RUNTIME_SA?.trim() ||
      "887772634501-compute@developer.gserviceaccount.com";
    const accessors = [
      `serviceAccount:${consoleSa}`,
      "serviceAccount:bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com",
    ];
    try {
      const [policy] = await client.getIamPolicy({ resource: name });
      const bindings = policy.bindings || [];
      let accessor = bindings.find((b) => b.role === "roles/secretmanager.secretAccessor");
      if (!accessor) {
        accessor = { role: "roles/secretmanager.secretAccessor", members: [] };
        bindings.push(accessor);
      }
      const set = new Set(accessor.members || []);
      for (const m of accessors) set.add(m);
      accessor.members = [...set];
      policy.bindings = bindings;
      await client.setIamPolicy({ resource: name, policy });
    } catch (e) {
      console.error(
        `plaid_access_token IAM bind failed for ${secretId}: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }
}

export async function loadAccessTokenSecret(itemId: string): Promise<string> {
  const { SecretManagerServiceClient } = await import("@google-cloud/secret-manager");
  const client = new SecretManagerServiceClient();
  const project = process.env.GCP_PROJECT || process.env.BQ_PROJECT || "jarvis-bhaga-prod";
  const name = `projects/${project}/secrets/${accessTokenSecretId(itemId)}/versions/latest`;
  const [version] = await client.accessSecretVersion({ name });
  const data = version.payload?.data;
  if (!data) throw new Error(`Empty secret for item ${itemId}`);
  return Buffer.isBuffer(data) ? data.toString("utf8") : String(data);
}
