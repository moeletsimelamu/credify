import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ApplicationStatus, CurrencyPortfolio } from "@/api/types";
import { useMonthlyPortfolio, usePortfolio } from "@/features/admin/api";
import { addDays, lastMonths, startOfMonth, startOfYear } from "@/shared/lib/dates";
import { usePageTitle } from "@/shared/lib/forms";
import {
  businessToday,
  compactTick,
  formatCount,
  formatDate,
  formatMoney,
  formatPercent,
} from "@/shared/lib/format";
import { statusLabel } from "@/shared/lib/labels";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  MoneyText,
  PageHeader,
  QueryView,
  SelectField,
  Stat,
  TextField,
} from "@/shared/ui";
import { ChartTooltip } from "@/shared/ui/chart";

type Preset = "30d" | "month" | "quarter" | "ytd" | "year" | "custom";

function rangeFor(preset: Preset, today: string): { from: string; to: string } {
  switch (preset) {
    case "30d":
      return { from: addDays(today, -29), to: today };
    case "month":
      return { from: startOfMonth(today), to: today };
    case "quarter":
      return { from: addDays(today, -89), to: today };
    case "ytd":
      return { from: startOfYear(today), to: today };
    default:
      return { from: addDays(today, -364), to: today };
  }
}

const PRESETS: Array<{ value: Preset; label: string }> = [
  { value: "30d", label: "Last 30 days" },
  { value: "month", label: "This month" },
  { value: "quarter", label: "Last 90 days" },
  { value: "ytd", label: "Year to date" },
  { value: "year", label: "Last 12 months" },
  { value: "custom", label: "Custom range" },
];

const BUCKET_COLOURS: Record<string, string> = {
  CURRENT: "var(--color-positive)",
  "1-30": "var(--color-warning)",
  "31-60": "#b86e00",
  "61-90": "#c2452b",
  "90+": "var(--color-critical)",
};

function Kpis({ data }: { data: CurrencyPortfolio }) {
  const c = data.currency;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        label="Approval rate"
        value={formatPercent(data.approval_rate)}
        detail={`${formatCount(data.decisions_approved)} approved · ${formatCount(data.decisions_declined)} declined`}
      />
      <Stat
        label="Principal disbursed"
        value={<MoneyText amount={data.principal_disbursed} currency={c} />}
        detail={`${formatCount(data.loans_disbursed)} loans in the period`}
      />
      <Stat
        label="Outstanding principal"
        value={<MoneyText amount={data.outstanding_principal} currency={c} />}
        detail={
          <>
            In arrears: <MoneyText amount={data.outstanding_principal_in_arrears} currency={c} />
          </>
        }
      />
      <Stat
        label="Arrears rate"
        value={formatPercent(data.arrears_rate)}
        tone={data.arrears_rate && Number(data.arrears_rate) > 0.1 ? "critical" : undefined}
        detail="Share of outstanding principal in arrears"
      />
      <Stat
        label="Collection rate"
        value={formatPercent(data.collection_rate)}
        detail={
          <>
            Collected <MoneyText amount={data.amount_collected} currency={c} /> of{" "}
            <MoneyText amount={data.amount_due} currency={c} /> due
          </>
        }
      />
      <Stat
        label="Repayments received"
        value={<MoneyText amount={data.repayments_received} currency={c} />}
      />
    </div>
  );
}

function ApplicationsByStatus({ data }: { data: CurrencyPortfolio }) {
  const rows = Object.entries(data.applications_by_status as Record<string, number>)
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({ status, label: statusLabel(status as ApplicationStatus), count }));
  if (rows.length === 0) return <EmptyState title="No applications in this period" />;
  return (
    <figure className="flex flex-col gap-3">
      <div
        role="img"
        aria-label={`Applications by status: ${rows.map((r) => `${r.label} ${r.count}`).join(", ")}`}
        className="h-56"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
            <XAxis
              type="number"
              allowDecimals={false}
              tick={{ fontSize: 11, fill: "var(--color-muted)" }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={170}
              tick={{ fontSize: 12, fill: "var(--color-fg)" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--color-sunken)" }}
              content={
                <ChartTooltip<{ label: string; count: number }>
                  render={(row) => `${row.label}: ${formatCount(row.count)}`}
                />
              }
            />
            <Bar
              dataKey="count"
              name="Applications"
              fill="var(--color-ink-700)"
              radius={2}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">
        <table>
          <caption>Applications by status</caption>
          <tbody>
            {rows.map((row) => (
              <tr key={row.status}>
                <th scope="row">{row.label}</th>
                <td>{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </figcaption>
    </figure>
  );
}

function ArrearsBuckets({ data }: { data: CurrencyPortfolio }) {
  const c = data.currency;
  const rows = data.arrears_buckets.map((bucket) => ({
    ...bucket,
    value: Number(bucket.outstanding_principal),
  }));
  return (
    <div className="flex flex-col gap-4">
      <div
        role="img"
        aria-label={`Outstanding principal by arrears bucket: ${data.arrears_buckets.map((b) => `${b.bucket} ${formatMoney(b.outstanding_principal, c)}`).join(", ")}`}
        className="h-48"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ left: 8, right: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--color-line)" />
            <XAxis
              dataKey="bucket"
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
                <ChartTooltip<{ bucket: string; accounts: number; outstanding_principal: string }>
                  render={(row) => (
                    <>
                      <p className="font-medium">
                        {row.bucket} · {formatCount(row.accounts)} accounts
                      </p>
                      <p className="figure">{formatMoney(row.outstanding_principal, c)}</p>
                    </>
                  )}
                />
              }
            />
            <Bar dataKey="value" isAnimationActive={false} radius={[3, 3, 0, 0]}>
              {rows.map((row) => (
                <Cell
                  key={row.bucket}
                  fill={BUCKET_COLOURS[row.bucket] ?? "var(--color-ink-700)"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable
        caption="Arrears buckets"
        dense
        rows={data.arrears_buckets}
        rowKey={(row) => row.bucket}
        columns={[
          {
            key: "bucket",
            header: "Days past due",
            cell: (r) => (r.bucket === "CURRENT" ? "Current" : r.bucket),
          },
          {
            key: "accounts",
            header: "Accounts",
            numeric: true,
            cell: (r) => formatCount(r.accounts),
          },
          {
            key: "principal",
            header: "Outstanding principal",
            numeric: true,
            cell: (r) => <MoneyText amount={r.outstanding_principal} currency={c} />,
          },
        ]}
      />
    </div>
  );
}

function MonthlyTrend({ currency, today }: { currency: string; today: string }) {
  const months = lastMonths(6, today);
  const results = useMonthlyPortfolio(months);
  if (results.some((result) => result.isPending))
    return <LoadingState label="Loading the monthly trend" />;
  const failed = results.find((result) => result.isError);
  if (failed)
    return (
      <ErrorState error={failed.error} onRetry={() => results.forEach((r) => void r.refetch())} />
    );
  const rows = months.map((month, index) => {
    const data = results[index]?.data?.currencies.find((entry) => entry.currency === currency);
    return {
      label: month.label,
      disbursed: data?.principal_disbursed ?? "0.00",
      disbursedValue: Number(data?.principal_disbursed ?? 0),
      collection: data?.collection_rate ?? null,
      collectionValue:
        data?.collection_rate === null || data?.collection_rate === undefined
          ? null
          : Number(data.collection_rate) * 100,
      loans: data?.loans_disbursed ?? 0,
    };
  });
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <div
          role="img"
          aria-label={`Principal disbursed by month: ${rows.map((r) => `${r.label} ${formatMoney(r.disbursed, currency)}`).join(", ")}`}
          className="h-44"
        >
          <p className="mb-1 text-xs font-medium text-muted">Principal disbursed</p>
          <ResponsiveContainer width="100%" height="90%">
            <BarChart data={rows} margin={{ left: 8, right: 8 }}>
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: "var(--color-muted)" }}
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
                  <ChartTooltip<{ label: string; disbursed: string }>
                    render={(row) => `${row.label}: ${formatMoney(row.disbursed, currency)}`}
                  />
                }
              />
              <Bar
                dataKey="disbursedValue"
                fill="var(--color-accent)"
                radius={[3, 3, 0, 0]}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div
          role="img"
          aria-label={`Collection rate by month: ${rows.map((r) => `${r.label} ${formatPercent(r.collection)}`).join(", ")}`}
          className="h-44"
        >
          <p className="mb-1 text-xs font-medium text-muted">Collection rate</p>
          <ResponsiveContainer width="100%" height="90%">
            <LineChart data={rows} margin={{ left: 8, right: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--color-line)" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: "var(--color-muted)" }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                domain={[0, 100]}
                unit="%"
                tick={{ fontSize: 11, fill: "var(--color-muted)" }}
                tickLine={false}
                axisLine={false}
                width={44}
              />
              <Tooltip
                content={
                  <ChartTooltip<{ label: string; collection: string | null }>
                    render={(row) => `${row.label}: ${formatPercent(row.collection)} collected`}
                  />
                }
              />
              <Line
                type="monotone"
                dataKey="collectionValue"
                stroke="var(--color-ink-700)"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <DataTable
        caption="Monthly trend"
        dense
        rows={rows}
        rowKey={(row) => row.label}
        columns={[
          { key: "month", header: "Month", cell: (r) => r.label },
          {
            key: "loans",
            header: "Loans disbursed",
            numeric: true,
            cell: (r) => formatCount(r.loans),
          },
          {
            key: "disbursed",
            header: "Principal disbursed",
            numeric: true,
            cell: (r) => <MoneyText amount={r.disbursed} currency={currency} />,
          },
          {
            key: "collection",
            header: "Collection rate",
            numeric: true,
            cell: (r) => formatPercent(r.collection),
          },
        ]}
      />
    </div>
  );
}

export function PortfolioPage() {
  usePageTitle("Portfolio");
  const today = businessToday();
  const [preset, setPreset] = useState<Preset>("year");
  const [custom, setCustom] = useState(() => rangeFor("year", today));
  const range = preset === "custom" ? custom : rangeFor(preset, today);
  const valid = range.from <= range.to;
  const portfolio = usePortfolio(range.from, valid ? range.to : range.from);

  return (
    <>
      <PageHeader
        title="Portfolio"
        description="Origination, arrears and collections across the simulated loan book."
      />
      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="w-52">
          <SelectField
            label="Period"
            value={preset}
            onChange={(event) => setPreset(event.target.value as Preset)}
            options={PRESETS}
          />
        </div>
        {preset === "custom" && (
          <>
            <TextField
              label="From"
              type="date"
              max={today}
              value={custom.from}
              onChange={(event) => setCustom((r) => ({ ...r, from: event.target.value }))}
            />
            <TextField
              label="To"
              type="date"
              max={today}
              value={custom.to}
              error={valid ? undefined : "The end date must be on or after the start date"}
              onChange={(event) => setCustom((r) => ({ ...r, to: event.target.value }))}
            />
          </>
        )}
      </div>
      <QueryView
        query={portfolio}
        loadingLabel="Loading portfolio analytics"
        isEmpty={(data) => data.currencies.length === 0}
        empty={
          <EmptyState title="No activity yet">
            Seed the demo data or process some applications to see analytics.
          </EmptyState>
        }
      >
        {(data) => (
          <div className="flex flex-col gap-8">
            <p className="text-sm text-muted">
              {formatDate(data.date_from)} – {formatDate(data.date_to)} · collections counted
              through {formatDate(data.collections_through)}
              {data.note ? ` · ${data.note}` : ""}
            </p>
            {data.currencies.map((entry) => (
              <section
                key={entry.currency}
                aria-labelledby={`portfolio-${entry.currency}`}
                className="flex flex-col gap-6"
              >
                <h2 id={`portfolio-${entry.currency}`} className="text-lg font-semibold">
                  {entry.currency} book
                </h2>
                <Kpis data={entry} />
                <div className="grid gap-6 xl:grid-cols-2">
                  <Card
                    title="Applications by status"
                    description="Applications created in the period."
                  >
                    <ApplicationsByStatus data={entry} />
                  </Card>
                  <Card title="Arrears" description="Current position of the outstanding book.">
                    <ArrearsBuckets data={entry} />
                  </Card>
                </div>
                <Card title="Last six months">
                  <MonthlyTrend currency={entry.currency} today={today} />
                </Card>
              </section>
            ))}
          </div>
        )}
      </QueryView>
    </>
  );
}
