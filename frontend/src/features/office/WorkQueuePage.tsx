import { Link, useSearchParams } from "react-router";

import { useApplications, useProducts, useStaffDirectory } from "@/features/applications/api";
import { useCurrentUser } from "@/features/auth/useAuth";
import { cx } from "@/shared/lib/cx";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDateTime, humanize } from "@/shared/lib/format";
import { APPLICATION_STATUSES, statusLabel } from "@/shared/lib/labels";
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
import { filtersFor, VIEWS, type View } from "./queueFilters";

export function WorkQueuePage() {
  usePageTitle("Work queue");
  const user = useCurrentUser();
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.find((v) => v.key === params.get("view"))?.key ??
    (user.role === "CREDIT_ANALYST" ? "decision" : "open")) as View;
  const status = params.get("status") ?? "";
  const productId = params.get("product") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  const applications = useApplications(filtersFor(view, user.id, status, productId, page));
  const products = useProducts(true);
  const staff = useStaffDirectory();
  const productName = (id: string) => products.data?.items.find((p) => p.id === id)?.name ?? "—";
  const staffName = (id: string | null) =>
    id
      ? id === user.id
        ? "You"
        : (staff.data?.find((s) => s.id === id)?.full_name ?? "—")
      : "Unassigned";

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in changes)) next.delete("page");
    setParams(next);
  };

  return (
    <>
      <PageHeader
        title="Work queue"
        description="Submitted applications, oldest submissions last. Open one to review it."
      />
      <nav aria-label="Queue views" className="mb-4 flex flex-wrap gap-1">
        {VIEWS.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={view === item.key}
            onClick={() => update({ view: item.key })}
            className={cx(
              "rounded-full px-3 py-1.5 text-sm",
              view === item.key
                ? "bg-ink-900 text-white"
                : "bg-surface text-muted ring-1 ring-line hover:text-fg",
            )}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="mb-4 grid max-w-2xl gap-3 sm:grid-cols-2">
        <SelectField
          label="Status"
          value={status}
          onChange={(event) => update({ status: event.target.value })}
          placeholder="Any status in this view"
          options={APPLICATION_STATUSES.filter((s) => s !== "DRAFT").map((s) => ({
            value: s,
            label: statusLabel(s),
          }))}
        />
        <SelectField
          label="Product"
          value={productId}
          onChange={(event) => update({ product: event.target.value })}
          placeholder="All products"
          options={(products.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
        />
      </div>
      <Card>
        <QueryView
          query={applications}
          loadingLabel="Loading applications"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState title="Nothing in this view">
              Try another view or clear the filters.
            </EmptyState>
          }
        >
          {(data) => (
            <>
              <DataTable
                caption="Applications"
                rows={data.items}
                rowKey={(row) => row.id}
                columns={[
                  {
                    key: "ref",
                    header: "Reference",
                    cell: (row) => (
                      <Link
                        to={`/office/applications/${row.id}`}
                        className="figure font-medium whitespace-nowrap text-accent underline-offset-2 hover:underline"
                      >
                        {row.reference}
                      </Link>
                    ),
                  },
                  {
                    key: "product",
                    header: "Product",
                    cell: (row) => productName(row.loan_product_id),
                  },
                  {
                    key: "amount",
                    header: "Amount",
                    numeric: true,
                    cell: (row) => (
                      <MoneyText amount={row.requested_amount} currency={row.currency} />
                    ),
                  },
                  {
                    key: "term",
                    header: "Term",
                    numeric: true,
                    cell: (row) => `${row.requested_term_months} mo`,
                  },
                  { key: "purpose", header: "Purpose", cell: (row) => humanize(row.purpose) },
                  {
                    key: "status",
                    header: "Status",
                    cell: (row) => (
                      <StatusBadge status={row.status} label={statusLabel(row.status)} />
                    ),
                  },
                  {
                    key: "officer",
                    header: "Officer",
                    cell: (row) => staffName(row.assigned_officer_id),
                  },
                  {
                    key: "submitted",
                    header: "Submitted",
                    cell: (row) => formatDateTime(row.submitted_at),
                  },
                ]}
              />
              <Pagination
                page={data.page}
                pageSize={data.page_size}
                total={data.total}
                onPage={(next) => update({ page: String(next) })}
              />
            </>
          )}
        </QueryView>
      </Card>
    </>
  );
}
