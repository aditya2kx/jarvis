/**
 * Process-wide singleton. Next.js bundles route handlers and pages into separate module
 * graphs, so plain module state would give `/api/cache-stats` (and each layer) its own
 * empty cache.
 */
export function shared<T>(name: string, init: () => T): T {
  const g = globalThis as unknown as Record<symbol, T | undefined>;
  const key = Symbol.for(`operator-console.query-cache.${name}`);
  return (g[key] ??= init());
}
