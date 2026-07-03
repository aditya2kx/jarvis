import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run deploys the standalone server output (see ../../docs/operator-console/EXECUTION.md §5.4).
  output: "standalone",
};

export default nextConfig;
