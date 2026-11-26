import { useState } from "react";
import { Link, useParams } from "react-router";

import type { LoanAccount, Transaction } from "@/api/types";
import { useAuth } from "@/features/auth/useAuth";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDate, formatDateTime, formatPercent, humanize } from "@/shared/lib/format";
import {
  Badge,
  Card,
  DataTable,
  EmptyState,
  KeyValueList,
  MoneyText,
  PageHeader,
  QueryView,
  SelectField,
  Stat,
  StatusBadge,
  Tabs,
} from "@/shared/ui";

import {
  useCharges,
  useLoan,
  useLoanSchedule,
  useScheduleVersion,
  useScheduleVersions,
  useTransactions,
} from "./api";
import { RepaymentPanel, SettlementPanel } from "./PaymentPanels";
import { LoanScheduleTable } from "./ScheduleTables";

function LoanSummary({ account }: { account: LoanAccount }) {
  const c = account.currency;
  const inArrears = account.days_past_due > 0;
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Outstanding principal"
          value={<MoneyText amount={account.outstanding_principal} currency={c} />}
        />
        <Stat
          label="Monthly instalment"
          value={<MoneyText amount={account.instalment_amount} currency={c} />}
        />
        <Stat
          label="Arrears"
          value={<MoneyText amount={account.arrears_amount} currency={c} />}
          tone={inArrears ? "critical" : undefined}
          detail={
            inArrears
              ? `${account.days_past_due} days past due · bucket ${account.arrears_bucket}`
              : "Up to date"
          }
        />
        <Stat
          label="Matures"
          value={formatDate(account.maturity_date)}
          detail={`${account.term_months} months from ${formatDate(account.disbursed_on)}`}
        />
      </div>
      <Card title="Terms">
        <KeyValueList
          columns={3}
          items={[
            {
              label: "Approved amount",
              value: <MoneyText amount={account.approved_amount} currency={c} />,
            },
            {
              label: "Initiation fee",
              value: <MoneyText amount={account.initiation_fee} currency={c} />,
            },
            { label: "Principal", value: <MoneyText amount={account.principal} currency={c} /> },
            {
              label: "Interest rate",
              value: `${formatPercent(account.annual_interest_rate)} a year`,
              numeric: true,
            },
            {
              label: "Monthly service fee",
              value: <MoneyText amount={account.monthly_service_fee} currency={c} />,
            },
            {
              label: "Late fee",
              value: (
                <>
                  <MoneyText amount={account.late_fee} currency={c} /> after{" "}
                  {account.grace_period_days} days' grace
                </>
              ),
            },
            {
              label: "Early settlement fee",
              value: formatPercent(account.early_settlement_fee_rate),
              numeric: true,
            },
            { label: "First instalment due", value: formatDate(account.first_due_date) },
            { label: "Position as of", value: formatDate(account.position_as_of) },
          ]}
        />
      </Card>
    </div>
  );
}

function ScheduleTab({ account }: { account: LoanAccount }) {
  const schedule = useLoanSchedule(account.id);
  const versions = useScheduleVersions(account.id);
  const [version, setVersion] = useState<number | null>(null);
  const history = useScheduleVersion(account.id, version);
  return (
    <div className="flex flex-col gap-6">
      <Card
        title="Effective schedule"
        description="Each schedule version replaces the instalments from its first instalment on; a prepayment creates a new version."
      >
        <QueryView query={schedule} loadingLabel="Loading the schedule">
          {(data) => <LoanScheduleTable instalments={data.instalments} currency={data.currency} />}
        </QueryView>
      </Card>
      <Card title="Schedule versions">
        <QueryView
          query={versions}
          isEmpty={(rows) => rows.length === 0}
          empty={<EmptyState title="No schedule versions" />}
        >
          {(rows) => (
            <div className="flex flex-col gap-4">
              <DataTable
                caption="Schedule versions"
                rows={rows}
                rowKey={(row) => String(row.version)}
                columns={[
                  { key: "v", header: "Version", numeric: true, cell: (r) => `v${r.version}` },
                  { key: "reason", header: "Reason", cell: (r) => humanize(r.reason) },
                  {
                    key: "from",
                    header: "From instalment",
                    numeric: true,
                    cell: (r) => r.first_instalment_number,
                  },
                  {
                    key: "engine",
                    header: "Engine",
                    cell: (r) => <span className="figure text-xs">{r.engine_version}</span>,
                  },
                  { key: "at", header: "Created", cell: (r) => formatDateTime(r.created_at) },
                  {
                    key: "current",
                    header: "Current",
                    cell: (r) => (r.is_current ? <Badge tone="accent">Current</Badge> : "—"),
                  },
                ]}
              />
              <div className="max-w-xs">
                <SelectField
                  label="View a version as it was created"
                  value={version === null ? "" : String(version)}
                  onChange={(event) =>
                    setVersion(event.target.value ? Number(event.target.value) : null)
                  }
                  placeholder="Choose a version…"
                  options={rows.map((row) => ({
                    value: String(row.version),
                    label: `v${row.version} · ${humanize(row.reason)}`,
                  }))}
                />
              </div>
              {version !== null && (
                <QueryView query={history} loadingLabel="Loading the version">
                  {(detail) => (
                    <DataTable
                      caption={`Schedule version ${detail.version}`}
                      captionHidden={false}
                      dense
                      rows={detail.instalments}
                      rowKey={(row) => String(row.number)}
                      columns={[
                        { key: "n", header: "#", numeric: true, cell: (r) => r.number },
                        { key: "due", header: "Due", cell: (r) => formatDate(r.due_date) },
                        {
                          key: "open",
                          header: "Opening",
                          numeric: true,
                          cell: (r) => (
                            <MoneyText amount={r.opening_balance} currency={account.currency} />
                          ),
                        },
                        {
                          key: "total",
                          header: "Total due",
                          numeric: true,
                          cell: (r) => (
                            <MoneyText amount={r.total_due} currency={account.currency} />
                          ),
                        },
                        {
                          key: "close",
                          header: "Closing",
                          numeric: true,
                          cell: (r) => (
                            <MoneyText amount={r.closing_balance} currency={account.currency} />
                          ),
                        },
                        {
                          key: "status",
                          header: "Status",
                          cell: (r) => <StatusBadge status={r.status} />,
                        },
                      ]}
                    />
                  )}
                </QueryView>
              )}
            </div>
          )}
        </QueryView>
      </Card>
    </div>
  );
}

function Allocations({ transaction }: { transaction: Transaction }) {
  const parts = transaction.allocations ?? [];
  if (parts.length === 0) return <span className="text-muted">—</span>;
  return (
    <ul className="flex flex-col gap-0.5 text-xs">
      {parts.map((part, index) => (
        <li key={index} className="flex justify-between gap-3">
          <span className="text-muted">{humanize(part.component)}</span>
          <MoneyText amount={part.amount} currency={transaction.currency} />
        </li>
      ))}
    </ul>
  );
}

function HistoryTab({ account }: { account: LoanAccount }) {
  const transactions = useTransactions(account.id);
  const charges = useCharges(account.id);
  const c = account.currency;
  return (
    <div className="flex flex-col gap-6">
      <Card
        title="Transactions"
        description="Every movement on the account, oldest first, with how each payment was allocated."
      >
        <QueryView
          query={transactions}
          isEmpty={(rows) => rows.length === 0}
          empty={<EmptyState title="No transactions yet" />}
        >
          {(rows) => (
            <DataTable<Transaction>
              caption="Transactions"
              rows={rows}
              rowKey={(row) => row.id}
              columns={[
                { key: "date", header: "Value date", cell: (r) => formatDate(r.value_date) },
                { key: "type", header: "Type", cell: (r) => humanize(r.transaction_type) },
                {
                  key: "ref",
                  header: "Reference",
                  cell: (r) => <span className="figure text-xs">{r.reference}</span>,
                },
                { key: "channel", header: "Channel", cell: (r) => humanize(r.channel) },
                {
                  key: "amount",
                  header: "Amount",
                  numeric: true,
                  cell: (r) => <MoneyText amount={r.amount} currency={c} />,
                },
                {
                  key: "alloc",
                  header: "Allocated to",
                  cell: (r) => <Allocations transaction={r} />,
                },
                {
                  key: "balance",
                  header: "Balance after",
                  numeric: true,
                  cell: (r) => <MoneyText amount={r.balance_after} currency={c} />,
                },
              ]}
            />
          )}
        </QueryView>
      </Card>
      <Card title="Fees and charges">
        <QueryView
          query={charges}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState title="No charges">
              Late fees appear here if an instalment is missed.
            </EmptyState>
          }
        >
          {(rows) => (
            <DataTable
              caption="Charges"
              rows={rows}
              rowKey={(row) => row.id}
              columns={[
                { key: "date", header: "Charged", cell: (r) => formatDate(r.charged_on) },
                { key: "type", header: "Type", cell: (r) => humanize(r.charge_type) },
                {
                  key: "amount",
                  header: "Amount",
                  numeric: true,
                  cell: (r) => <MoneyText amount={r.amount} currency={c} />,
                },
                {
                  key: "paid",
                  header: "Paid",
                  numeric: true,
                  cell: (r) => <MoneyText amount={r.amount_paid} currency={c} />,
                },
              ]}
            />
          )}
        </QueryView>
      </Card>
    </div>
  );
}

function LoanView({ account, area }: { account: LoanAccount; area: "portal" | "office" }) {
  const { can } = useAuth();
  const [tab, setTab] = useState("summary");
  const staff = area === "office";
  const canPay = staff ? can("repayment:record") : can("repayment:record_own");
  usePageTitle(account.account_number);
  return (
    <>
      <PageHeader
        eyebrow={
          staff ? (
            <Link to="/office/loans" className="hover:underline">
              ← Loan book
            </Link>
          ) : (
            <Link to="/portal" className="hover:underline">
              ← Overview
            </Link>
          )
        }
        title={<span className="figure">{account.account_number}</span>}
        description={
          staff ? (
            <Link
              to={`/office/applications/${account.application_id}?tab=loan`}
              className="text-accent hover:underline"
            >
              View the application
            </Link>
          ) : (
            "Simulated loan account. No real money is lent or repaid."
          )
        }
        actions={<StatusBadge status={account.status} />}
      />
      <Tabs
        label="Loan account"
        value={tab}
        onValueChange={setTab}
        items={[
          {
            value: "summary",
            label: "Summary",
            content: (
              <div className="flex flex-col gap-6">
                <LoanSummary account={account} />
                {canPay && (
                  <div className="grid gap-6 lg:grid-cols-2">
                    <RepaymentPanel account={account} staff={staff} />
                    <SettlementPanel account={account} staff={staff} />
                  </div>
                )}
              </div>
            ),
          },
          { value: "schedule", label: "Schedule", content: <ScheduleTab account={account} /> },
          {
            value: "history",
            label: "Payments & charges",
            content: <HistoryTab account={account} />,
          },
        ]}
      />
    </>
  );
}

function LoanRoute({ area }: { area: "portal" | "office" }) {
  const { loanId = "" } = useParams();
  const loan = useLoan(loanId);
  return (
    <QueryView query={loan} loadingLabel="Loading the loan">
      {(account) => <LoanView account={account} area={area} />}
    </QueryView>
  );
}

export function PortalLoanPage() {
  return <LoanRoute area="portal" />;
}

export function OfficeLoanPage() {
  return <LoanRoute area="office" />;
}
