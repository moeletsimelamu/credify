import { Link } from "react-router";

import { useMyApplicant } from "@/features/applicants/api";
import { useApplications, useProducts } from "@/features/applications/api";
import { useLoans } from "@/features/loans/api";
import { useCurrentUser } from "@/features/auth/useAuth";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDate, formatDateTime } from "@/shared/lib/format";
import { statusLabel } from "@/shared/lib/labels";
import {
  ButtonLink,
  Card,
  DataTable,
  EmptyState,
  MoneyText,
  PageHeader,
  QueryView,
  StatusBadge,
} from "@/shared/ui";

export function PortalHome() {
  usePageTitle("Overview");
  const user = useCurrentUser();
  const me = useMyApplicant();
  const hasProfile = Boolean(me.data);
  const applications = useApplications({ page_size: 50 });
  const loans = useLoans({ page_size: 20 });
  const products = useProducts();
  const productName = (id: string) =>
    products.data?.items.find((product) => product.id === id)?.name ?? "Loan";

  return (
    <>
      <PageHeader
        eyebrow={`Welcome, ${user.full_name}`}
        title="Your loans and applications"
        actions={
          hasProfile && (
            <ButtonLink to="/portal/applications/new" variant="primary">
              Start a new application
            </ButtonLink>
          )
        }
      />

      {me.isSuccess && !hasProfile && (
        <Card title="Get started" className="mb-6">
          <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm">
            <li>
              <Link
                to="/portal/profile"
                className="font-medium text-accent underline-offset-2 hover:underline"
              >
                Add your details
              </Link>{" "}
              — name, contact and address.
            </li>
            <li>Add your employment, income, expenses and existing credit.</li>
            <li>Choose a loan product and submit an application.</li>
          </ol>
        </Card>
      )}

      <div className="flex flex-col gap-6">
        <Card title="My loans">
          <QueryView
            query={loans}
            isEmpty={(page) => page.items.length === 0}
            empty={
              <EmptyState title="No loans yet">
                When an application is approved and paid out, your loan appears here.
              </EmptyState>
            }
          >
            {(page) => (
              <ul className="grid gap-3 sm:grid-cols-2">
                {page.items.map((loan) => (
                  <li key={loan.id} className="rounded-card border border-line p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <Link
                          to={`/portal/loans/${loan.id}`}
                          className="figure font-medium whitespace-nowrap text-accent underline-offset-2 hover:underline"
                        >
                          {loan.account_number}
                        </Link>
                        <p className="text-xs text-muted">
                          Matures {formatDate(loan.maturity_date)}
                        </p>
                      </div>
                      <StatusBadge status={loan.status} />
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <dt className="text-xs text-muted">Outstanding principal</dt>
                        <dd>
                          <MoneyText amount={loan.outstanding_principal} currency={loan.currency} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted">Monthly instalment</dt>
                        <dd>
                          <MoneyText amount={loan.instalment_amount} currency={loan.currency} />
                        </dd>
                      </div>
                      {loan.days_past_due > 0 && (
                        <div className="col-span-2 text-critical">
                          <dt className="text-xs">In arrears</dt>
                          <dd>
                            <MoneyText amount={loan.arrears_amount} currency={loan.currency} /> ·{" "}
                            {loan.days_past_due} days past due
                          </dd>
                        </div>
                      )}
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>

        <Card title="My applications">
          <QueryView
            query={applications}
            isEmpty={(page) => page.items.length === 0}
            empty={
              <EmptyState
                title="No applications yet"
                action={
                  hasProfile ? (
                    <ButtonLink to="/portal/applications/new" variant="primary">
                      Start an application
                    </ButtonLink>
                  ) : (
                    <ButtonLink to="/portal/profile" variant="primary">
                      Add my details first
                    </ButtonLink>
                  )
                }
              >
                Applications you start are saved as drafts until you submit them.
              </EmptyState>
            }
          >
            {(page) => (
              <DataTable
                caption="My applications"
                rows={page.items}
                rowKey={(row) => row.id}
                columns={[
                  {
                    key: "ref",
                    header: "Reference",
                    cell: (row) => (
                      <Link
                        to={`/portal/applications/${row.id}`}
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
                  {
                    key: "status",
                    header: "Status",
                    cell: (row) => (
                      <StatusBadge status={row.status} label={statusLabel(row.status)} />
                    ),
                  },
                  {
                    key: "updated",
                    header: "Last updated",
                    cell: (row) => formatDateTime(row.updated_at),
                  },
                ]}
              />
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
