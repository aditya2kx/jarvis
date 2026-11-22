/**
 * The last steps taken on this device: screens, taps, calls to the API and errors. They stay on the
 * device and go out only inside a feedback report, so a problem can be traced to what led to it.
 */
export interface Step {
  at: string;
  kind: "screen" | "action" | "api" | "error";
  what: string;
  status?: number;
  ms?: number;
}

const KEY = "store-tasks-steps";
const MAX = 100;
const MAX_TEXT = 300;

let steps: Step[] = (() => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as Step[];
  } catch {
    return [];
  }
})();
let where: Record<string, unknown> = {};

export function track(kind: Step["kind"], what: string, extra: Pick<Step, "status" | "ms"> = {}) {
  steps = [...steps, { at: new Date().toISOString(), kind, what: what.slice(0, MAX_TEXT), ...extra }].slice(-MAX);
  try {
    sessionStorage.setItem(KEY, JSON.stringify(steps));
  } catch {
    // Storage full or blocked: the steps still live in memory.
  }
}

export const recentSteps = () => steps;

/** What the current screen shows: the date, list and task a report is about. */
export function setWhere(next: Record<string, unknown>) {
  const label = [next.screen, next.date, next.shift, next.task].filter(Boolean).join(" · ");
  if (label !== [where.screen, where.date, where.shift, where.task].filter(Boolean).join(" · ")) track("screen", label);
  where = next;
}

export function snapshot(): Record<string, unknown> {
  const build = document.querySelector<HTMLScriptElement>("script[type=module][src]")?.src.split("/").pop() ?? "dev";
  return {
    ...where,
    online: navigator.onLine,
    app: build,
    installed: window.matchMedia("(display-mode: standalone)").matches,
    screen_size: `${window.innerWidth}x${window.innerHeight}`,
    device: navigator.userAgent,
    sent_at: new Date().toISOString(),
  };
}

export function recordErrors() {
  window.addEventListener("error", (e) => track("error", `${e.message} @ ${e.filename?.split("/").pop()}:${e.lineno}`));
  window.addEventListener("unhandledrejection", (e) => track("error", `unhandled: ${String((e.reason as Error)?.message ?? e.reason)}`));
}
