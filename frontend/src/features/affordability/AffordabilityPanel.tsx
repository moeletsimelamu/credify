import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ApplicationStatus, AffordabilityAssessment, RuleResult } from "@/api/types";
import { useAffordability, useRunAffordability } from "@/features/applications/api";
import { useCan } from "@/features/auth/useAuth";
import {
  compactTick,
  formatDateTime,
  formatMoney,
  formatPercent,
  humanize,
} from "@/shared/lib/format";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  FormMessage,
  KeyValueList,
  MoneyText,
  QueryView,
  SelectField,
  StatusBadge,
} from "@/shared/ui";
import { waterfallSteps, type WaterfallStep } from "./waterfall";
import { ASSESSABLE_STATUSES } from "@/features/applications/workflow";
import { ChartTooltip } from "@/shared/ui/chart";

const BAR_COLOURS = {
  total: "var(--color-ink-700)",
  deduction: "var(--color-warning)",
  result: "var(--color-accent)",
  negative: "var(--color-critical)",
};

export function AffordabilityWaterfall({ assessment }: { assessment: AffordabilityAssessment }) {
  const steps = waterfallSteps(assessment);
  const c = assessment.currency;
  const buffer = Number(assessment.required_buffer);
  const summary = steps.map((step) => `${step.label} ${formatMoney(step.amount, c)}`).join(", ");
  return (
    <figure className="flex flex-col gap-3">
      <div
        role="img"
        aria-label={`Affordability waterfall: ${summary}. Required buffer ${formatMoney(assessment.required_buffer, c)}.`}
        className="h-64"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={steps} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--color-line)" />
            <XAxis
              dataKey="label"
              interval={0}
              tick={{ fontSize: 12, fill: "var(--color-muted)" }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tickFormatter={compactTick}
              tick={{ fontSize: 11, fill: "var(--color-muted)" }}
              tickLine={false}
              axisLine={false}
              width={48}
            />
            <Tooltip
              cursor={{ fill: "var(--color-sunken)" }}
              content={
                <ChartTooltip<WaterfallStep>
                  render={(step) => (
                    <>
                      <p className="font-medium">{step.label}</p>
                      <p className="figure">{formatMoney(step.amount, c)}</p>
                    </>
                  )}
                />
              }
            />
            <ReferenceLine
              y={buffer}
              stroke="var(--color-critical)"
              strokeDasharray="4 3"
              label={{
                value: "Required buffer",
                position: "insideTopRight",
                fontSize: 11,
                fill: "var(--color-critical)",
              }}
            />
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="value" stackId="w" isAnimationActive={false} radius={[3, 3, 0, 0]}>
              {steps.map((step) => (
                <Cell
                  key={step.key}
                  fill={
                    step.kind === "result" && step.value < buffer
                      ? BAR_COLOURS.negative
                      : BAR_COLOURS[step.kind]
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption>
        <table className="w-full text-sm">
          <caption className="sr-only">Monthly affordability breakdown</caption>
          <tbody>
            {steps.map((step) => (
              <tr key={step.key} className="border-b border-line last:border-0">
                <th scope="row" className="py-1.5 text-left font-normal text-muted">
                  {step.kind === "deduction" ? "less " : ""}
                  {step.label}
                </th>
                <td
                  className={
                    step.kind === "result"
                      ? "figure py-1.5 text-right font-semibold"
                      : "figure py-1.5 text-right"
                  }
                >
                  {step.kind === "deduction" ? "−" : ""}
                  {formatMoney(step.amount, c)}
                </td>
              </tr>
            ))}
            <tr>
              <th scope="row" className="py-1.5 text-left font-normal text-muted">
                Required buffer
              </th>
              <td className="figure py-1.5 text-right">
                {formatMoney(assessment.required_buffer, c)}
              </td>
            </tr>
          </tbody>
        </table>
      </figcaption>
    </figure>
  );
}

function ruleStatus(rule: RuleResult) {
  if (rule.passed === null) return <Badge>Not evaluated</Badge>;
  return rule.passed ? (
    <Badge tone="positive">Passed</Badge>
  ) : (
    <Badge tone={rule.effect === "UNAFFORDABLE" ? "critical" : "warning"}>Failed</Badge>
  );
}

function AssessmentDetail({ assessment }: { assessment: AffordabilityAssessment }) {
  const c = assessment.currency;
  const rules = assessment.rule_results as unknown as RuleResult[];
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={assessment.outcome} />
        <span className="text-sm text-muted">
          Version {assessment.version} · {humanize(assessment.run_reason)} ·{" "}
          {formatDateTime(assessment.created_at)} · calculator {assessment.calculator_version}
        </span>
      </div>
      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <AffordabilityWaterfall assessment={assessment} />
        <KeyValueList
          items={[
            {
              label: "Gross monthly income",
              value: <MoneyText amount={assessment.gross_monthly_income} currency={c} />,
            },
            {
              label: "Declared living expenses",
              value: <MoneyText amount={assessment.declared_living_expenses} currency={c} />,
            },
            {
              label: "Expense norm (minimum applied)",
              value: <MoneyText amount={assessment.expense_norm} currency={c} />,
            },
            {
              label: "Disposable income",
              value: <MoneyText amount={assessment.disposable_income} currency={c} />,
            },
            {
              label: "Debt-to-income",
              value: formatPercent(assessment.debt_to_income_ratio),
              numeric: true,
            },
            {
              label: "Repayment burden",
              value: formatPercent(assessment.repayment_burden_ratio),
              numeric: true,
            },
            {
              label: "New-loan burden",
              value: formatPercent(assessment.new_loan_burden_ratio),
              numeric: true,
            },
          ]}
        />
      </div>
      <DataTable<RuleResult>
        caption="Affordability rules"
        captionHidden={false}
        rows={rules}
        rowKey={(rule) => rule.rule_id}
        columns={[
          {
            key: "rule",
            header: "Rule",
            cell: (r) => <span className="font-medium">{r.name}</span>,
          },
          { key: "status", header: "Result", cell: ruleStatus },
          { key: "actual", header: "Actual", numeric: true, cell: (r) => r.actual ?? "—" },
          { key: "threshold", header: "Threshold", numeric: true, cell: (r) => r.threshold ?? "—" },
          {
            key: "explanation",
            header: "Explanation",
            cell: (r) => <span className="text-muted">{r.explanation}</span>,
          },
        ]}
      />
    </div>
  );
}

/** Versioned affordability assessments for one application (staff). */
export function AffordabilityPanel({
  applicationId,
  status,
}: {
  applicationId: string;
  status: ApplicationStatus;
}) {
  const assessments = useAffordability(applicationId);
  const run = useRunAffordability(applicationId);
  const canRun = useCan("affordability:run") && ASSESSABLE_STATUSES.includes(status);
  const [selected, setSelected] = useState<string>("");

  return (
    <Card
      title="Affordability assessment"
      description="Deterministic rules applied by the affordability calculator. Each run is kept as a new version."
      actions={
        canRun && (
          <Button
            variant="secondary"
            size="sm"
            loading={run.isPending}
            onClick={() => run.mutate(undefined, { onSuccess: () => setSelected("") })}
          >
            Run a new assessment
          </Button>
        )
      }
    >
      <FormMessage error={run.error} />
      <QueryView
        query={assessments}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="No assessment yet">
            An assessment runs automatically when the application is submitted.
          </EmptyState>
        }
      >
        {(rows) => {
          const ordered = [...rows].sort((a, b) => b.version - a.version);
          const current = ordered.find((row) => row.id === selected) ?? ordered[0]!;
          return (
            <div className="flex flex-col gap-4">
              {ordered.length > 1 && (
                <div className="max-w-xs">
                  <SelectField
                    label="Version"
                    value={current.id}
                    onChange={(event) => setSelected(event.target.value)}
                    options={ordered.map((row, index) => ({
                      value: row.id,
                      label: `v${row.version} · ${humanize(row.outcome)}${index === 0 ? " (latest)" : ""}`,
                    }))}
                  />
                </div>
              )}
              <AssessmentDetail assessment={current} />
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}
