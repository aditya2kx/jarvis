import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run deploys the standalone server output (see ../../docs/operator-console/EXECUTION.md §5.4).
  output: "standalone",
  // Version-skew protection: a tab opened on an older build hard-reloads on its next
  // navigation instead of stalling (Next 16 docs: next-config-js/deploymentId).
  deploymentId: process.env.NEXT_DEPLOYMENT_ID || undefined,
};

export default nextConfig;
