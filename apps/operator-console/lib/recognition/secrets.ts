import "server-only";

/** Latest version of a Secret Manager secret as a string (ADC locally, runtime SA on Cloud Run). */
export async function readSecret(secretId: string): Promise<string> {
  const { SecretManagerServiceClient } = await import("@google-cloud/secret-manager");
  const client = new SecretManagerServiceClient();
  const project = process.env.GCP_PROJECT || process.env.BQ_PROJECT || "jarvis-bhaga-prod";
  const [version] = await client.accessSecretVersion({
    name: `projects/${project}/secrets/${secretId}/versions/latest`,
  });
  const data = version.payload?.data;
  if (!data) throw new Error(`Secret ${secretId} is empty`);
  return Buffer.isBuffer(data) ? data.toString("utf8") : String(data);
}
