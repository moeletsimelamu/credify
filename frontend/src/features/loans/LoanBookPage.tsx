import { Link, useSearchParams } from "react-router";

import type { LoanStatus } from "@/api/types";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDate } from "@/shared/lib/format";
import { LOAN_STATUSES, options } from "@/shared/lib/labels";
import {
  Card,
  DataTable,
  EmptyState,
  MoneyText,
  PageHeader,
  Pagination,
  QueryView,
  SelectField,
  StatusBadge,
} from "@/shared/ui";

import { useLoans } from "./api";

const ARREARS_FILTERS = [
  { value: "1", label: "Any arrears (1+ days)" },
  { value: "31", label: "31+ days" },
  { value: "61", label: "61+ days" },
  { value: "91", label: "90+ days" },
];

export function LoanBookPage() {
  usePageTitle("Loan book");
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const minDays = params.get("dpd") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const loans = useLoans({
    status: status ? [status as LoanStatus] : undefined,
    min_days_past_due: minDays ? Number(minDays) : undefined,
    page,
    page_size: 25,
  });
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  return (
    <>
      <PageHeader
        title="Loan book"
        description="Every loan account and its position as of the last arrears run."
      />
      <div className="mb-4 grid max-w-2xl gap-3 sm:grid-cols-2">
        <SelectField
          label="Status"
          value={status}
          onChange={(e) => update("status", e.target.value)}
          placeholder="All statuses"
          options={options(LOAN_STATUSES)}
        />
        <SelectField
          label="Days past due"
          value={minDays}
          onChange={(e) => update("dpd", e.target.value)}
          placeholder="Any"
          options={ARREARS_FILTERS}
        />
      </div>
      <Card>
        <QueryView
          query={loans}
          loadingLabel="Loading loans"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState title="No loans match">
              Loans appear once approved applications are disbursed.
            </EmptyState>
          }
        >
          {(data) => (
            <>
              <DataTable
                caption="Loan accounts"
                rows={data.items}
                rowKey={(row) => row.id}
                rowClassName={(row) => (row.days_past_due > 0 ? "bg-critical-soft/40" : undefined)}
                columns={[
                  {
                    key: "account",
                    header: "Account",
                    cell: (r) => (
                      <Link
                        to={`/office/loans/${r.id}`}
                        className="figure font-medium whitespace-nowrap text-accent underline-offset-2 hover:underline"
                      >
                        {r.account_number}
                      </Link>
                    ),
                  },
                  {
                    key: "status",
                    header: "Status",
                    cell: (r) => <StatusBadge status={r.status} />,
                  },
                  {
                    key: "principal",
                    header: "Principal",
                    numeric: true,
                    cell: (r) => <MoneyText amount={r.principal} currency={r.currency} />,
                  },
                  {
                    key: "outstanding",
                    header: "Outstanding",
                    numeric: true,
                    cell: (r) => (
                      <MoneyText amount={r.outstanding_principal} currency={r.currency} />
                    ),
                  },
                  {
                    key: "arrears",
                    header: "Arrears",
                    numeric: true,
                    cell: (r) => <MoneyText amount={r.arrears_amount} currency={r.currency} />,
                  },
                  {
                    key: "dpd",
                    header: "Days past due",
                    numeric: true,
                    cell: (r) => r.days_past_due,
                  },
                  { key: "bucket", header: "Bucket", cell: (r) => r.arrears_bucket },
                  {
                    key: "disbursed",
                    header: "Disbursed",
                    cell: (r) => formatDate(r.disbursed_on),
                  },
                ]}
              />
              <Pagination
                page={data.page}
                pageSize={data.page_size}
                total={data.total}
                onPage={(next) => update("page", String(next))}
              />
            </>
          )}
        </QueryView>
      </Card>
    </>
  );
}
