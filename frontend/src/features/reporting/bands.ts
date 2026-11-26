import { formatPercent } from "@/shared/lib/format";

export interface Bands {
  bands?: string[];
  cuts?: number[];
  guidance?: Record<string, string>;
}

/** Band ranges from the cut-offs, e.g. A: below 4%, B: 4–7%, … E: 25% and above. */
export function bandRanges(
  cutoffs: Bands,
): Array<{ band: string; range: string; guidance: string }> {
  const bands = cutoffs.bands ?? [];
  const cuts = cutoffs.cuts ?? [];
  return bands.map((band, index) => {
    const low = index === 0 ? null : cuts[index - 1];
    const high = index < cuts.length ? cuts[index] : null;
    const range =
      low === null || low === undefined
        ? `below ${formatPercent(high ?? 0)}`
        : high === null || high === undefined
          ? `${formatPercent(low)} and above`
          : `${formatPercent(low)} – ${formatPercent(high)}`;
    return { band, range, guidance: cutoffs.guidance?.[band] ?? "" };
  });
}
