/** Gaps larger than this are something else (an on-screen keyboard, split view), not the bug. */
const MAX_GAP_PX = 120;

const clamp = (gap: number) => (gap > 0.5 && gap <= MAX_GAP_PX ? Math.round(gap) : 0);

function probe(style: Partial<CSSStyleDeclaration>): DOMRect {
  const el = document.createElement("div");
  Object.assign(el.style, { position: "fixed", left: "0", width: "0", visibility: "hidden", pointerEvents: "none" }, style);
  document.body.appendChild(el);
  const rect = el.getBoundingClientRect();
  el.remove();
  return rect;
}

const isStandalone = () =>
  (navigator as Navigator & { standalone?: boolean }).standalone === true ||
  window.matchMedia("(display-mode: standalone)").matches;

/**
 * iOS 26 home-screen web apps sometimes lay the page out shorter than the screen, so `100%`
 * and `bottom: 0` stop above the bottom edge. Measures where they actually end and publishes
 * each shortfall: `--vp-gap` for fixed `bottom: 0` (sheets, toasts, photo viewer) and
 * `--dvh-gap` for the page height. Both stay unset outside the home-screen app.
 */
export function watchViewportGap(): void {
  if (!isStandalone()) return;
  const root = document.documentElement;
  const update = () => {
    // iOS keeps screen.width/height in portrait terms whatever the orientation.
    const landscape = window.matchMedia("(orientation: landscape)").matches;
    const screenH = landscape ? Math.min(screen.width, screen.height) : Math.max(screen.width, screen.height);
    const fixedBottom = probe({ bottom: "0", height: "0" }).top;
    const page = probe({ top: "0", height: "100dvh" }).height;
    root.style.setProperty("--vp-gap", `${clamp(screenH - fixedBottom)}px`);
    root.style.setProperty("--dvh-gap", `${clamp(screenH - page)}px`);
  };
  update();
  // iOS can settle the viewport after load without a resize event.
  [300, 1000, 2500].forEach((ms) => window.setTimeout(update, ms));
  ["resize", "orientationchange", "pageshow"].forEach((e) => window.addEventListener(e, update));
  document.addEventListener("visibilitychange", update);
}
