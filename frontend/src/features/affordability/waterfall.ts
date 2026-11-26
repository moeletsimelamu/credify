import type { AffordabilityAssessment } from "@/api/types";

export interface WaterfallStep {
  key: string;
  label: string;
  amount: string;
  /** Chart geometry only. Labels always show the server's exact amount. */
  base: number;
  value: number;
  kind: "total" | "deduction" | "result";
}

/**
 * Net income → living expenses → existing debt → proposed instalment → residual, so the
 * reader sees *why* the residual is what it is (ARCHITECTURE.md §8.1). The bar positions are
 * plotted with JavaScript numbers; every printed figure is the server's decimal string.
 */
export function waterfallSteps(a: AffordabilityAssessment): WaterfallStep[] {
  const net = Number(a.net_monthly_income);
  const living = Number(a.applied_living_expenses);
  const debt = Number(a.existing_debt_instalments);
  const instalment = Number(a.proposed_instalment);
  const residual = Number(a.residual_income);
  const afterLiving = net - living;
  const afterDebt = afterLiving - debt;
  return [
    {
      key: "net",
      label: "Net income",
      amount: a.net_monthly_income,
      base: 0,
      value: net,
      kind: "total",
    },
    {
      key: "living",
      label: "Living expenses",
      amount: a.applied_living_expenses,
      base: Math.min(afterLiving, net),
      value: living,
      kind: "deduction",
    },
    {
      key: "debt",
      label: "Existing debt",
      amount: a.existing_debt_instalments,
      base: Math.min(afterDebt, afterLiving),
      value: debt,
      kind: "deduction",
    },
    {
      key: "instalment",
      label: "New instalment",
      amount: a.proposed_instalment,
      base: Math.min(residual, afterDebt),
      value: instalment,
      kind: "deduction",
    },
    {
      key: "residual",
      label: "Residual",
      amount: a.residual_income,
      base: 0,
      value: residual,
      kind: "result",
    },
  ];
}
