import type { ReactNode } from "react";

/**
 * Tooltip body for Recharts, used as `<Tooltip content={<ChartTooltip render={...} />} />`.
 * Recharts injects `active` and `payload`; render(row) gets the hovered data row, so tooltips
 * show the server's exact figures rather than the chart's plotted numbers.
 */
export function ChartTooltip<T>({
  render,
  active,
  payload,
}: {
  render: (row: T) => ReactNode;
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: unknown }>;
}) {
  const row = payload?.[0]?.payload as T | undefined;
  if (!active || !row) return null;
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-card">
      {render(row)}
    </div>
  );
}
