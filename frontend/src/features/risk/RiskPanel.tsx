import { useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ApplicationStatus, Contribution, Explanation, RiskAssessment } from "@/api/types";
import { useBureauReport, useRiskAssessments, useRunRisk } from "@/features/applications/api";
import { useCan } from "@/features/auth/useAuth";
import { formatDateTime, formatProbability, humanize } from "@/shared/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  FormMessage,
  KeyValueList,
  Notice,
  QueryView,
  RiskBandBadge,
  SelectField,
  StatusBadge,
} from "@/shared/ui";
import { rankedContributions } from "./contributions";
import { ASSESSABLE_STATUSES } from "@/features/applications/workflow";
import { ChartTooltip } from "@/shared/ui/chart";

const DEFAULT_ADVISORY =
  "Experimental model output on synthetic data. Advisory only: a person makes every lending decision.";

function formatLogOdds(value: number): string {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}`;
}

/** Signed factor contributions: right raises the modelled risk, left lowers it. */
export function ContributionChart({ explanation }: { explanation: Explanation }) {
  const rows = rankedContributions(explanation).filter((row) => row.log_odds !== 0);
  const top = rows.slice(0, 10);
  // Centre the axis on zero so "raises" and "lowers" always sit either side of it.
  const extent = Math.max(
    0.1,
    Math.ceil(Math.max(...top.map((row) => Math.abs(row.log_odds))) * 10) / 10,
  );
  return (
    <figure className="flex flex-col gap-4">
      <div
        role="img"
        aria-label={`Factor contributions in log-odds. ${top.map((row) => `${row.label} ${formatLogOdds(row.log_odds)}`).join("; ")}.`}
        style={{ height: Math.max(180, top.length * 30 + 40) }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={top} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
            <XAxis
              type="number"
              domain={[-extent, extent]}
              ticks={[-extent, -extent / 2, 0, extent / 2, extent]}
              tickFormatter={(value: number) => formatLogOdds(value)}
              tick={{ fontSize: 11, fill: "var(--color-muted)" }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={190}
              tick={{ fontSize: 12, fill: "var(--color-fg)" }}
              tickLine={false}
              axisLine={false}
            />
            <ReferenceLine x={0} stroke="var(--color-line-strong)" />
            <Tooltip
              cursor={{ fill: "var(--color-sunken)" }}
              content={
                <ChartTooltip<Contribution>
                  render={(row) => (
                    <>
                      <p className="font-medium">
                        {row.label}: {row.display_value}
                      </p>
                      <p className="figure">{formatLogOdds(row.log_odds)} log-odds</p>
                    </>
                  )}
                />
              }
            />
            <Bar dataKey="log_odds" isAnimationActive={false} radius={2}>
              {top.map((row) => (
                <Cell
                  key={row.feature}
                  fill={row.log_odds > 0 ? "var(--color-critical)" : "var(--color-positive)"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-muted">
        Bars to the right raise the modelled risk, bars to the left lower it, relative to an average
        applicant in the training data. {explanation.caveat}
      </figcaption>
      <DataTable<Contribution>
        caption="All factor contributions"
        captionHidden={false}
        dense
        rows={rankedContributions(explanation)}
        rowKey={(row) => row.feature}
        columns={[
          { key: "factor", header: "Factor", cell: (r) => r.label },
          { key: "value", header: "Value", cell: (r) => r.display_value },
          {
            key: "direction",
            header: "Effect",
            cell: (r) =>
              r.direction === "increases" ? (
                <span className="text-critical">Raises risk</span>
              ) : r.direction === "decreases" ? (
                <span className="text-positive">Lowers risk</span>
              ) : (
                <span className="text-muted">No effect</span>
              ),
          },
          {
            key: "log_odds",
            header: "Log-odds",
            numeric: true,
            cell: (r) => formatLogOdds(r.log_odds),
          },
        ]}
      />
    </figure>
  );
}

function AssessmentView({ assessment }: { assessment: RiskAssessment }) {
  const explanation = assessment.explanation as unknown as Explanation | null;
  if (assessment.status === "FAILED") {
    return (
      <Notice tone="critical" title="The model could not score this application">
        {assessment.failure_reason ?? "No reason was recorded."} Review can continue without a
        score; the decision must then be made on the other evidence.
      </Notice>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted uppercase">
            Probability of default
          </p>
          <p className="figure mt-1 text-2xl font-semibold">
            {formatProbability(assessment.probability_of_default)}
          </p>
        </div>
        <div>
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Risk band</p>
          <p className="mt-2">
            <RiskBandBadge band={assessment.risk_band} />
          </p>
        </div>
        <div>
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Review threshold</p>
          <p className="figure mt-1 text-sm">
            {formatProbability(assessment.decision_threshold)} ·{" "}
            {assessment.above_threshold ? (
              <span className="font-medium text-warning">above — review carefully</span>
            ) : (
              <span className="text-muted">below</span>
            )}
          </p>
        </div>
      </div>
      {explanation && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <p className="text-sm font-medium">Raising the risk most</p>
              <ul className="mt-1 list-disc pl-5 text-sm text-fg/90">
                {explanation.top_increasing.length ? (
                  explanation.top_increasing.map((text) => <li key={text}>{text}</li>)
                ) : (
                  <li>None</li>
                )}
              </ul>
            </div>
            <div>
              <p className="text-sm font-medium">Lowering the risk most</p>
              <ul className="mt-1 list-disc pl-5 text-sm text-fg/90">
                {explanation.top_decreasing.length ? (
                  explanation.top_decreasing.map((text) => <li key={text}>{text}</li>)
                ) : (
                  <li>None</li>
                )}
              </ul>
            </div>
          </div>
          <ContributionChart explanation={explanation} />
        </>
      )}
    </div>
  );
}

function BureauReportCard({ applicationId }: { applicationId: string }) {
  const report = useBureauReport(applicationId);
  return (
    <Card
      title="Credit bureau report"
      description="Simulated: generated deterministically from the applicant's synthetic reference."
    >
      <QueryView
        query={report}
        isEmpty={(data) => data === null}
        empty={
          <EmptyState title="No bureau report yet">
            One is pulled when the application is first scored.
          </EmptyState>
        }
      >
        {(data) =>
          data && (
            <KeyValueList
              columns={2}
              items={[
                { label: "Provider", value: humanize(data.provider) },
                { label: "Simulator", value: data.simulator_version },
                {
                  label: "Missed payments (12 months)",
                  value: data.missed_payments_12m,
                  numeric: true,
                },
                {
                  label: "Months since last delinquency",
                  value: data.months_since_delinquency ?? "None on record",
                  numeric: true,
                },
                { label: "Pulled", value: formatDateTime(data.created_at) },
              ]}
            />
          )
        }
      </QueryView>
    </Card>
  );
}

/** The risk workspace. Everything here is visibly marked as experimental and advisory. */
export function RiskPanel({
  applicationId,
  status,
}: {
  applicationId: string;
  status: ApplicationStatus;
}) {
  const assessments = useRiskAssessments(applicationId);
  const run = useRunRisk(applicationId);
  const canRun = useCan("risk:run") && ASSESSABLE_STATUSES.includes(status);
  const [selected, setSelected] = useState("");

  return (
    <div className="flex flex-col gap-6">
      <section
        aria-labelledby="risk-heading"
        className="rounded-card border-2 border-dashed border-advisory/40 bg-advisory-soft/60"
      >
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-dashed border-advisory/30 px-5 py-3.5">
          <div>
            <p className="text-xs font-semibold tracking-wider text-advisory uppercase">
              Experimental model output — advisory only
            </p>
            <h2 id="risk-heading" className="text-base font-semibold">
              Credit risk assessment
            </h2>
          </div>
          {canRun && (
            <Button
              variant="secondary"
              size="sm"
              loading={run.isPending}
              onClick={() => run.mutate(undefined, { onSuccess: () => setSelected("") })}
            >
              Score again
            </Button>
          )}
        </header>
        <div className="flex flex-col gap-4 px-5 py-4">
          <FormMessage error={run.error} />
          <QueryView
            query={assessments}
            isEmpty={(rows) => rows.length === 0}
            empty={
              <EmptyState title="Not scored yet">
                Applications are scored automatically when they are submitted.
              </EmptyState>
            }
          >
            {(rows) => {
              const ordered = [...rows].sort((a, b) => b.version - a.version);
              const current = ordered.find((row) => row.id === selected) ?? ordered[0]!;
              return (
                <>
                  <p className="text-sm text-fg/90">
                    {current.advisory_notice ?? DEFAULT_ADVISORY}
                  </p>
                  <div className="flex flex-wrap items-end gap-4">
                    {ordered.length > 1 && (
                      <div className="w-64">
                        <SelectField
                          label="Version"
                          value={current.id}
                          onChange={(event) => setSelected(event.target.value)}
                          options={ordered.map((row, index) => ({
                            value: row.id,
                            label: `v${row.version} · ${humanize(row.status)}${index === 0 ? " (latest)" : ""}`,
                          }))}
                        />
                      </div>
                    )}
                    <p className="flex items-center gap-2 pb-2 text-xs text-muted">
                      <StatusBadge status={current.status} /> {humanize(current.run_reason)} ·{" "}
                      {formatDateTime(current.created_at)}
                    </p>
                  </div>
                  <AssessmentView assessment={current} />
                </>
              );
            }}
          </QueryView>
        </div>
      </section>
      <BureauReportCard applicationId={applicationId} />
    </div>
  );
}
