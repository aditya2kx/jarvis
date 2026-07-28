/** Sole operator entry URL host (project-number form). Issue #208. */
export const CANONICAL_CONSOLE_HOST = "operator-console-887772634501.us-central1.run.app";

/**
 * Cloud Run also serves this hash form. Unsupported for operators — do not
 * cross-redirect after IAP (cookie jars are host-scoped; Issue #208).
 */
export const HASH_CONSOLE_HOST = "operator-console-4yl5izovxq-uc.a.run.app";
