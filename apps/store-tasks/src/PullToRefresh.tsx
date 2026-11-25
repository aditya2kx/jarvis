import { RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx } from "./ui";

/** Pull distance (after resistance) that reloads on release. */
const TRIGGER_PX = 64;
const MAX_PX = 96;

/**
 * The page's scrolling list under the finger, when it is at its top and nothing between the
 * finger and it is scrolled or floating (a sheet, the photo viewer).
 */
function pullableFrom(target: EventTarget | null): boolean {
  const main = (target as Element | null)?.closest?.("main");
  if (!main || main.scrollTop > 0) return false;
  for (let el = target as HTMLElement | null; el && el !== main; el = el.parentElement) {
    if (el.scrollTop > 0 || getComputedStyle(el).position === "fixed") return false;
  }
  return true;
}

/**
 * Pull down at the top of a list to reload the app — fresh tasks and any new version. Ticks
 * waiting to send survive the reload (they are kept on the device). The page itself never
 * scrolls (each screen's list does), so neither Safari nor the home-screen app offers this.
 */
export function PullToRefresh() {
  const chip = useRef<HTMLDivElement>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let start: { x: number; y: number } | null = null;
    let pull = 0;
    const show = (px: number, settle: boolean) => {
      const el = chip.current;
      if (!el) return;
      el.style.transition = settle ? "transform 200ms var(--glide), opacity 200ms ease" : "none";
      el.style.transform = `translate(-50%, ${px - 44}px) rotate(${(px / TRIGGER_PX) * 270}deg)`;
      el.style.opacity = String(Math.min(1, px / TRIGGER_PX));
    };
    const onStart = (e: TouchEvent) => {
      const t = e.touches[0];
      start = e.touches.length === 1 && t && pullableFrom(e.target) ? { x: t.clientX, y: t.clientY } : null;
      pull = 0;
    };
    const onMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!start || !t) return;
      const dx = t.clientX - start.x;
      const dy = t.clientY - start.y;
      if (pull === 0 && Math.abs(dx) > Math.abs(dy)) {
        start = null;
        return;
      }
      pull = Math.min(MAX_PX, Math.max(0, dy * 0.5));
      show(pull, false);
    };
    const onEnd = () => {
      if (!start) return;
      start = null;
      if (pull >= TRIGGER_PX) {
        show(TRIGGER_PX, true);
        setRefreshing(true);
        window.location.reload();
      } else {
        show(0, true);
      }
    };
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchmove", onMove, { passive: true });
    document.addEventListener("touchend", onEnd);
    document.addEventListener("touchcancel", onEnd);
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  return (
    <div
      ref={chip}
      aria-hidden
      className="pointer-events-none fixed left-1/2 top-[calc(env(safe-area-inset-top)+0.75rem)] z-50 flex size-10 items-center justify-center rounded-full border border-border bg-card text-primary shadow-float"
      style={{ transform: "translate(-50%, -44px)", opacity: 0 }}
    >
      <RefreshCw className={cx("size-[18px]", refreshing && "animate-spin")} />
    </div>
  );
}
