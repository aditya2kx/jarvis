import { notFound } from "next/navigation";

/** Forecast UI removed from Operator Console (Issue #213). BQ/Grafana pipeline kept. */
export default function ForecastRemovedPage() {
  notFound();
}
