import "server-only";
import { BigQuery } from "@google-cloud/bigquery";

const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
const DATASET = process.env.JARVIS_DEV_BQ_DATASET ?? "jarvis_dev";
const TABLE = "console_iap_events";

let schemaReady: Promise<void> | null = null;

function client(): BigQuery {
  return new BigQuery({ projectId: PROJECT });
}

/** Idempotent CREATE TABLE for jarvis_dev.console_iap_events (Issue #194). */
export async function ensureIapEventsSchema(): Promise<void> {
  if (!schemaReady) {
    schemaReady = (async () => {
      const bq = client();
      const ds = bq.dataset(DATASET);
      const [exists] = await ds.exists();
      if (!exists) {
        await bq.createDataset(DATASET, { location: "US" });
      }
      await bq.query({
        query: `CREATE TABLE IF NOT EXISTS \`${PROJECT}.${DATASET}.${TABLE}\` (
          ts TIMESTAMP NOT NULL,
          event STRING NOT NULL,
          host STRING,
          ua STRING,
          referrer STRING,
          path STRING,
          url STRING,
          note STRING,
          dual_host BOOL,
          email_hash STRING
        )`,
      });
    })().catch((err) => {
      schemaReady = null;
      throw err;
    });
  }
  await schemaReady;
}

export type IapEventRow = {
  event: string;
  host?: string | null;
  ua?: string | null;
  referrer?: string | null;
  path?: string | null;
  url?: string | null;
  note?: string | null;
  dual_host?: boolean | null;
  email_hash?: string | null;
};

export async function insertIapEvent(row: IapEventRow): Promise<void> {
  await ensureIapEventsSchema();
  const bq = client();
  const errors = await bq.dataset(DATASET).table(TABLE).insert([
    {
      ts: new Date().toISOString(),
      event: row.event,
      host: row.host ?? null,
      ua: row.ua ?? null,
      referrer: row.referrer ?? null,
      path: row.path ?? null,
      url: row.url ?? null,
      note: row.note ?? null,
      dual_host: row.dual_host ?? null,
      email_hash: row.email_hash ?? null,
    },
  ]);
  // @google-cloud/bigquery insert returns void on success; PartialFailureError on fail
  void errors;
}
