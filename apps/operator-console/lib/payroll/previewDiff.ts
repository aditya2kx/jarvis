/** Console Hours / Total pay vs last ADP Preview snapshot (no URL). */

export const PREVIEW_HOURS_TOLERANCE = 0.5;
export const PREVIEW_PAY_TOLERANCE = 1.0;

export type PreviewDiffLine = {
  match: boolean;
  delta: number;
  preview: number;
  label: string;
};

export function previewLine(
  consoleValue: number,
  previewValue: number | null | undefined,
  kind: "hours" | "pay",
): PreviewDiffLine | null {
  if (previewValue == null || Number.isNaN(previewValue)) return null;
  const delta = consoleValue - previewValue;
  const tol =
    kind === "hours" ? PREVIEW_HOURS_TOLERANCE : PREVIEW_PAY_TOLERANCE;
  const match = Math.abs(delta) <= tol;
  if (kind === "hours") {
    const mag = Math.abs(delta).toFixed(2);
    return {
      match,
      delta,
      preview: previewValue,
      label: match
        ? `Matches last Preview (${previewValue.toFixed(2)}h)`
        : `${delta >= 0 ? "+" : "−"}${mag}h vs last Preview (${previewValue.toFixed(2)}h)`,
    };
  }
  const mag = Math.abs(delta).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
  const preview = previewValue.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
  return {
    match,
    delta,
    preview: previewValue,
    label: match
      ? `Matches last Preview (${preview})`
      : `${delta >= 0 ? "+" : "−"}${mag} vs last Preview (${preview})`,
  };
}
