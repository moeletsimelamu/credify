import type { Contribution, Explanation } from "@/api/types";

/** Contributions ordered by size, largest first. */
export function rankedContributions(explanation: Explanation): Contribution[] {
  return [...explanation.contributions].sort((a, b) => Math.abs(b.log_odds) - Math.abs(a.log_odds));
}
