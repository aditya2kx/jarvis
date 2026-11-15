import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { NextConfig } from "next";

// Next evaluates this file once per bundle, so the stamp must be deterministic (a timestamp
// would differ between the server and browser builds and make every table reload forever).
function sourceHash() {
  const hash = createHash("sha256");
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else hash.update(path).update(readFileSync(path));
    }
  };
  for (const dir of ["app", "components", "lib"]) walk(join(__dirname, dir));
  return hash.digest("hex").slice(0, 12);
}

const nextConfig: NextConfig = {
  serverExternalPackages: ["@electric-sql/pglite"],
  // Stamped into both server and browser bundles so open tables notice a deploy and refresh themselves.
  env: { POKER_BUILD: sourceHash() },
};

export default nextConfig;
