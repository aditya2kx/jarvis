"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCwIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** Pull distance (after resistance) that reloads on release. */
const TRIGGER_PX = 64;
const MAX_PX = 96;

/** Some element between the touch and the page scroller is scrolled down (a table, a list). */
function scrolledInside(target: EventTarget | null, scroller: HTMLElement): boolean {
  for (let el = target as HTMLElement | null; el && el !== scroller; el = el.parentElement) {
    if (el.scrollTop > 0) return true;
  }
  return false;
}

/**
 * Pull down at the top of the page to reload it. iOS home-screen web apps have no pull-to-refresh
 * of their own (Safari does), so this only arms there. Reloads the whole page, which also picks up
 * a new deploy.
 */
export function PullToRefresh({ children }: { children: React.ReactNode }) {
  const wrap = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLDivElement>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const standalone =
      (navigator as Navigator & { standalone?: boolean }).standalone === true ||
      window.matchMedia("(display-mode: standalone)").matches;
    const root = wrap.current;
    const scroller = root?.querySelector("main");
    if (!standalone || !root || !scroller) return;

    let start: { x: number; y: number } | null = null;
    let pull = 0;
    const show = (px: number, settle: boolean) => {
      const el = chip.current;
      if (!el) return;
      el.style.transition = settle ? "transform 200ms ease, opacity 200ms ease" : "none";
      el.style.transform = `translate(-50%, ${px - 44}px) rotate(${(px / TRIGGER_PX) * 270}deg)`;
      el.style.opacity = String(Math.min(1, px / TRIGGER_PX));
    };
    const onStart = (e: TouchEvent) => {
      const t = e.touches[0];
      start = e.touches.length === 1 && t && scroller.scrollTop <= 0 && !scrolledInside(e.target, scroller)
        ? { x: t.clientX, y: t.clientY }
        : null;
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
    root.addEventListener("touchstart", onStart, { passive: true });
    root.addEventListener("touchmove", onMove, { passive: true });
    root.addEventListener("touchend", onEnd);
    root.addEventListener("touchcancel", onEnd);
    return () => {
      root.removeEventListener("touchstart", onStart);
      root.removeEventListener("touchmove", onMove);
      root.removeEventListener("touchend", onEnd);
      root.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  return (
    <div ref={wrap} className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div
        ref={chip}
        aria-hidden
        className="pointer-events-none absolute top-0 left-1/2 z-20 flex size-9 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-md"
        style={{ transform: "translate(-50%, -44px)", opacity: 0 }}
      >
        <RefreshCwIcon className={cn("size-4", refreshing && "animate-spin text-foreground")} />
      </div>
      {children}
    </div>
  );
}
