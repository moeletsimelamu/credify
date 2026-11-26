import { useState } from "react";

import type { EffectiveInstalment, SchedulePreview } from "@/api/types";
import { formatDate, formatPercent } from "@/shared/lib/format";
import { Button, DataTable, KeyValueList, MoneyText, Notice, StatusBadge } from "@/shared/ui";

const PREVIEW_ROWS = 6;

/** An illustrative schedule: nothing is stored until a loan is opened. */
export function SchedulePreviewView({ preview }: { preview: SchedulePreview }) {
  const [showAll, setShowAll] = useState(false);
  const c = preview.currency;
  const rows = showAll ? preview.instalments : preview.instalments.slice(0, PREVIEW_ROWS);
  return (
    <div className="flex flex-col gap-4">
      <KeyValueList
        columns={3}
        items={[
          {
            label: "Monthly instalment",
            value: (
              <MoneyText
                amount={preview.level_instalment}
                currency={c}
                className="text-lg font-semibold"
              />
            ),
          },
          {
            label: "First payment",
            value: <MoneyText amount={preview.first_total_due} currency={c} />,
          },
          {
            label: "Interest rate",
            value: `${formatPercent(preview.annual_interest_rate)} a year`,
            numeric: true,
          },
          { label: "Amount borrowed", value: <MoneyText amount={preview.amount} currency={c} /> },
          {
            label: "Initiation fee (added to the loan)",
            value: <MoneyText amount={preview.initiation_fee} currency={c} />,
          },
          { label: "Principal", value: <MoneyText amount={preview.principal} currency={c} /> },
          {
            label: "Total interest",
            value: <MoneyText amount={preview.total_interest} currency={c} />,
          },
          {
            label: "Total service fees",
            value: <MoneyText amount={preview.total_service_fees} currency={c} />,
          },
          {
            label: "Total repayable",
            value: (
              <MoneyText amount={preview.total_repayable} currency={c} className="font-semibold" />
            ),
          },
        ]}
      />
      <Notice tone="info">
        Cost of credit <MoneyText amount={preview.cost_of_credit} currency={c} /> over{" "}
        {preview.term_months} months, assuming disbursement on{" "}
        {formatDate(preview.disbursement_date)}. Illustrative only: the final terms are set when a
        loan is opened.
      </Notice>
      <DataTable
        caption={`Repayment schedule, ${preview.term_months} instalments`}
        dense
        rows={rows}
        rowKey={(row) => String(row.number)}
        columns={[
          { key: "n", header: "#", numeric: true, cell: (r) => r.number },
          { key: "due", header: "Due", cell: (r) => formatDate(r.due_date) },
          {
            key: "open",
            header: "Opening",
            numeric: true,
            cell: (r) => <MoneyText amount={r.opening_balance} currency={c} />,
          },
          {
            key: "interest",
            header: "Interest",
            numeric: true,
            cell: (r) => <MoneyText amount={r.interest} currency={c} />,
          },
          {
            key: "principal",
            header: "Principal",
            numeric: true,
            cell: (r) => <MoneyText amount={r.principal} currency={c} />,
          },
          {
            key: "fee",
            header: "Fee",
            numeric: true,
            cell: (r) => <MoneyText amount={r.service_fee} currency={c} />,
          },
          {
            key: "total",
            header: "Total due",
            numeric: true,
            cell: (r) => <MoneyText amount={r.total_due} currency={c} className="font-medium" />,
          },
          {
            key: "close",
            header: "Closing",
            numeric: true,
            cell: (r) => <MoneyText amount={r.closing_balance} currency={c} />,
          },
        ]}
      />
      {preview.instalments.length > PREVIEW_ROWS && (
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAll((value) => !value)}
            aria-expanded={showAll}
          >
            {showAll
              ? "Show fewer instalments"
              : `Show all ${preview.instalments.length} instalments`}
          </Button>
        </div>
      )}
    </div>
  );
}

/** A loan's effective schedule: each row shows what is due, what is paid and its status. */
export function LoanScheduleTable({
  instalments,
  currency,
}: {
  instalments: EffectiveInstalment[];
  currency: string;
}) {
  const c = currency;
  return (
    <DataTable
      caption="Effective repayment schedule"
      dense
      rows={instalments}
      rowKey={(row) => `${row.number}-${row.schedule_version}`}
      rowClassName={(row) => (row.status === "OVERDUE" ? "bg-critical-soft/50" : undefined)}
      columns={[
        { key: "n", header: "#", numeric: true, cell: (r) => r.number },
        { key: "due", header: "Due", cell: (r) => formatDate(r.due_date) },
        {
          key: "interest",
          header: "Interest",
          numeric: true,
          cell: (r) => <MoneyText amount={r.interest_due} currency={c} />,
        },
        {
          key: "principal",
          header: "Principal",
          numeric: true,
          cell: (r) => <MoneyText amount={r.principal_due} currency={c} />,
        },
        {
          key: "fee",
          header: "Fee",
          numeric: true,
          cell: (r) => <MoneyText amount={r.service_fee_due} currency={c} />,
        },
        {
          key: "total",
          header: "Total due",
          numeric: true,
          cell: (r) => <MoneyText amount={r.total_due} currency={c} className="font-medium" />,
        },
        {
          key: "ipaid",
          header: "Interest paid",
          numeric: true,
          cell: (r) => <MoneyText amount={r.interest_paid} currency={c} />,
        },
        {
          key: "ppaid",
          header: "Principal paid",
          numeric: true,
          cell: (r) => <MoneyText amount={r.principal_paid} currency={c} />,
        },
        {
          key: "fpaid",
          header: "Fee paid",
          numeric: true,
          cell: (r) => <MoneyText amount={r.service_fee_paid} currency={c} />,
        },
        {
          key: "close",
          header: "Closing",
          numeric: true,
          cell: (r) => <MoneyText amount={r.closing_balance} currency={c} />,
        },
        { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        { key: "v", header: "Version", numeric: true, cell: (r) => `v${r.schedule_version}` },
      ]}
    />
  );
}
