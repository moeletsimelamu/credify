import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams, useSearchParams } from "react-router";
import { z } from "zod";

import type { ApplicationDetail, StaffMember } from "@/api/types";
import { AffordabilityPanel } from "@/features/affordability/AffordabilityPanel";
import { useApplicant, useFinancialProfile } from "@/features/applicants/api";
import { FinancialRecords } from "@/features/applicants/FinancialRecords";
import {
  useAddNote,
  useAffordability,
  useApplication,
  useApplicationLoan,
  useAssign,
  useDisburse,
  useNotes,
  useProducts,
  useRequestInformation,
  useRiskAssessments,
  useStaffDirectory,
  useTransition,
} from "@/features/applications/api";
import {
  ApplicationSummary,
  DocumentsPanel,
  InformationRequestsPanel,
  StatusTimeline,
} from "@/features/applications/components";
import { ASSESSABLE_STATUSES, can, workflowSteps } from "@/features/applications/workflow";
import { useAuth, useCurrentUser } from "@/features/auth/useAuth";
import { DecisionPanel } from "@/features/decisions/DecisionPanel";
import { AuditTrail } from "@/features/admin/AuditTrail";
import { RiskPanel } from "@/features/risk/RiskPanel";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDate, formatDateTime, formatProbability, humanize } from "@/shared/lib/format";
import { statusLabel } from "@/shared/lib/labels";
import {
  Button,
  ButtonLink,
  Card,
  EmptyState,
  FormMessage,
  KeyValueList,
  MoneyText,
  Notice,
  PageHeader,
  QueryView,
  RiskBandBadge,
  SelectField,
  StatusBadge,
  Stepper,
  Tabs,
  TextAreaField,
} from "@/shared/ui";

const TABS = [
  "overview",
  "financial",
  "affordability",
  "risk",
  "decision",
  "loan",
  "audit",
] as const;
type Tab = (typeof TABS)[number];

// --- Snapshot rail -----------------------------------------------------------------------

function SnapshotRail({
  application,
  staff,
}: {
  application: ApplicationDetail;
  staff: StaffMember[];
}) {
  const affordability = useAffordability(application.id);
  const risk = useRiskAssessments(application.id);
  const latestAffordability = [...(affordability.data ?? [])].sort(
    (a, b) => b.version - a.version,
  )[0];
  const latestRisk = [...(risk.data ?? [])].sort((a, b) => b.version - a.version)[0];
  const c = application.currency;
  const officer = staff.find((member) => member.id === application.assigned_officer_id);
  return (
    <Card as="aside" title="Snapshot" className="xl:sticky xl:top-6">
      <KeyValueList
        items={[
          {
            label: "Status",
            value: (
              <StatusBadge status={application.status} label={statusLabel(application.status)} />
            ),
          },
          {
            label: "Requested",
            value: <MoneyText amount={application.requested_amount} currency={c} />,
          },
          { label: "Term", value: `${application.requested_term_months} months`, numeric: true },
          {
            label: "Proposed instalment",
            value: latestAffordability ? (
              <MoneyText amount={latestAffordability.proposed_instalment} currency={c} />
            ) : (
              "Not assessed"
            ),
          },
          {
            label: "Residual income",
            value: latestAffordability ? (
              <span className="flex flex-wrap items-center gap-2">
                <MoneyText amount={latestAffordability.residual_income} currency={c} />
                <StatusBadge status={latestAffordability.outcome} />
              </span>
            ) : (
              "Not assessed"
            ),
          },
          {
            label: "Risk band (advisory)",
            value:
              latestRisk?.status === "SCORED" ? (
                <span className="flex items-center gap-2">
                  <RiskBandBadge band={latestRisk.risk_band} />
                  <span className="figure text-xs text-muted">
                    {formatProbability(latestRisk.probability_of_default)}
                  </span>
                </span>
              ) : latestRisk ? (
                "Scoring failed"
              ) : (
                "Not scored"
              ),
          },
          { label: "Assigned officer", value: officer?.full_name ?? "Unassigned" },
          { label: "Submitted", value: formatDateTime(application.submitted_at) },
        ]}
      />
    </Card>
  );
}

// --- Header actions ----------------------------------------------------------------------

function AssignControl({
  application,
  staff,
}: {
  application: ApplicationDetail;
  staff: StaffMember[];
}) {
  const user = useCurrentUser();
  const assign = useAssign(application.id);
  const officers = staff.filter((member) => member.role === "LOAN_OFFICER");
  const [choice, setChoice] = useState("");
  if (user.role === "LOAN_OFFICER") {
    if (application.assigned_officer_id === user.id) return null;
    return (
      <Button
        variant="secondary"
        loading={assign.isPending}
        onClick={() => assign.mutate({ version: application.version, officerId: user.id })}
      >
        Assign to me
      </Button>
    );
  }
  return (
    <div className="flex items-end gap-2">
      <SelectField
        label="Assign to"
        value={choice || application.assigned_officer_id || ""}
        onChange={(event) => setChoice(event.target.value)}
        placeholder="Choose an officer…"
        options={officers.map((member) => ({ value: member.id, label: member.full_name }))}
      />
      <Button
        variant="secondary"
        disabled={!choice || choice === application.assigned_officer_id}
        loading={assign.isPending}
        onClick={() => assign.mutate({ version: application.version, officerId: choice })}
      >
        Assign
      </Button>
    </div>
  );
}

const requestSchema = z.object({
  message: z
    .string()
    .trim()
    .min(1, "Say what you need from the applicant")
    .max(2000, "Keep it under 2,000 characters"),
});

function RequestInformationForm({ application }: { application: ApplicationDetail }) {
  const request = useRequestInformation(application.id);
  const form = useForm({ resolver: zodResolver(requestSchema), defaultValues: { message: "" } });
  const submit = form.handleSubmit((values) =>
    request
      .mutateAsync({ version: application.version, message: values.message })
      .then(() => form.reset()),
  );
  return (
    <form
      onSubmit={submit}
      noValidate
      className="mt-4 flex flex-col gap-3 rounded-card border border-line bg-sunken/50 p-4"
    >
      <TextAreaField
        label="Ask the applicant for more information"
        hint="Their profile unlocks so they can update it. Review pauses until they reply."
        rows={3}
        required
        error={form.formState.errors.message?.message}
        {...form.register("message")}
      />
      <FormMessage error={request.error} />
      <div>
        <Button type="submit" variant="secondary" loading={form.formState.isSubmitting}>
          Send request
        </Button>
      </div>
    </form>
  );
}

// --- Tabs --------------------------------------------------------------------------------

const noteSchema = z.object({
  body: z.string().trim().min(1, "Write a note").max(4000, "Keep it under 4,000 characters"),
});

function NotesPanel({ applicationId, staff }: { applicationId: string; staff: StaffMember[] }) {
  const { can: allowed } = useAuth();
  const notes = useNotes(applicationId);
  const add = useAddNote(applicationId);
  const form = useForm({ resolver: zodResolver(noteSchema), defaultValues: { body: "" } });
  const submit = form.handleSubmit((values) =>
    add.mutateAsync(values.body).then(() => form.reset()),
  );
  const name = (id: string | null) =>
    id ? (staff.find((s) => s.id === id)?.full_name ?? "Staff member") : "System";
  return (
    <Card title="Review notes" description="Internal. Applicants never see these.">
      <QueryView
        query={notes}
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState title="No notes yet" />}
      >
        {(rows) => (
          <ul className="flex flex-col gap-3">
            {[...rows].reverse().map((note) => (
              <li key={note.id} className="border-l-2 border-line-strong pl-3">
                <p className="text-xs text-muted">
                  {name(note.author_id)} · {humanize(note.note_type)} ·{" "}
                  {formatDateTime(note.created_at)}
                </p>
                <p className="text-sm whitespace-pre-line">{note.body}</p>
              </li>
            ))}
          </ul>
        )}
      </QueryView>
      {allowed("application:note") && (
        <form onSubmit={submit} noValidate className="mt-4 flex flex-col gap-3">
          <TextAreaField
            label="Add a note"
            rows={3}
            error={form.formState.errors.body?.message}
            {...form.register("body")}
          />
          <FormMessage error={add.error} />
          <div>
            <Button
              type="submit"
              variant="secondary"
              size="sm"
              loading={form.formState.isSubmitting}
            >
              Add note
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

function FinancialTab({ applicantId }: { applicantId: string }) {
  const profile = useFinancialProfile(applicantId);
  return (
    <QueryView query={profile} loadingLabel="Loading the financial profile">
      {(data) => <FinancialRecords applicantId={applicantId} profile={data} editable={false} />}
    </QueryView>
  );
}

function LoanTab({ application }: { application: ApplicationDetail }) {
  const approved = application.status === "APPROVED";
  const loan = useApplicationLoan(application.id, approved);
  const disburse = useDisburse(application.id);
  const { can: allowed } = useAuth();
  if (!approved) {
    return (
      <EmptyState title="No loan">
        A loan account is opened only after an application is approved.
      </EmptyState>
    );
  }
  return (
    <QueryView query={loan} loadingLabel="Loading the loan account">
      {(account) =>
        account ? (
          <Card
            title={<span className="figure">{account.account_number}</span>}
            actions={<ButtonLink to={`/office/loans/${account.id}`}>Open loan account</ButtonLink>}
          >
            <KeyValueList
              columns={3}
              items={[
                { label: "Status", value: <StatusBadge status={account.status} /> },
                {
                  label: "Principal",
                  value: <MoneyText amount={account.principal} currency={account.currency} />,
                },
                {
                  label: "Instalment",
                  value: (
                    <MoneyText amount={account.instalment_amount} currency={account.currency} />
                  ),
                },
                {
                  label: "Outstanding principal",
                  value: (
                    <MoneyText amount={account.outstanding_principal} currency={account.currency} />
                  ),
                },
                { label: "Disbursed", value: formatDate(account.disbursed_on) },
                { label: "Matures", value: formatDate(account.maturity_date) },
              ]}
            />
          </Card>
        ) : (
          <Card title="Ready to disburse">
            <p className="text-sm">
              The application is approved. Opening the loan creates the account, its first schedule
              and a simulated disbursement.
            </p>
            <FormMessage error={disburse.error} />
            {allowed("loan:disburse") ? (
              <Button
                className="mt-4"
                loading={disburse.isPending}
                onClick={() => disburse.mutate()}
              >
                Open loan and disburse (simulated)
              </Button>
            ) : (
              <p className="mt-3 text-sm text-muted">
                A loan officer or administrator opens the loan.
              </p>
            )}
          </Card>
        )
      }
    </QueryView>
  );
}

function Workspace({ application }: { application: ApplicationDetail }) {
  const { can: allowed } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t === params.get("tab")) ?? "overview") as Tab;
  const products = useProducts(true);
  const product = products.data?.items.find((p) => p.id === application.loan_product_id);
  const applicant = useApplicant(application.applicant_id);
  const staff = useStaffDirectory().data ?? [];
  const affordability = useAffordability(application.id);
  const risk = useRiskAssessments(application.id);
  const loan = useApplicationLoan(application.id, application.status === "APPROVED");
  const startReview = useTransition(application.id, "start-review");
  const actions = application.available_actions;
  const [askForInfo, setAskForInfo] = useState(false);

  const evidence = {
    affordability: [...(affordability.data ?? [])].sort((a, b) => b.version - a.version)[0],
    risk: [...(risk.data ?? [])]
      .filter((r) => r.status === "SCORED")
      .sort((a, b) => b.version - a.version)[0],
  };
  const applicantName = applicant.data
    ? `${applicant.data.first_name} ${applicant.data.last_name}`
    : "Applicant";
  usePageTitle(application.reference);

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/office" className="hover:underline">
            ← Work queue
          </Link>
        }
        title={
          <>
            <span className="figure">{application.reference}</span> · {applicantName}
          </>
        }
        description={
          applicant.data &&
          `Applicant ${applicant.data.synthetic_reference} · ${product?.name ?? ""}`
        }
        actions={
          <>
            {allowed("application:assign") && ASSESSABLE_STATUSES.includes(application.status) && (
              <AssignControl application={application} staff={staff} />
            )}
            {can(actions, "START_REVIEW") && (
              <Button
                loading={startReview.isPending}
                onClick={() => startReview.mutate({ version: application.version })}
              >
                Start review
              </Button>
            )}
            {can(actions, "REQUEST_INFORMATION") && (
              <Button
                variant="secondary"
                onClick={() => setAskForInfo((open) => !open)}
                aria-expanded={askForInfo}
              >
                Request information
              </Button>
            )}
          </>
        }
      />
      <div className="mb-5 flex flex-col gap-3">
        <Stepper
          label="Application progress"
          steps={workflowSteps(application.status, Boolean(loan.data))}
        />
        <FormMessage error={startReview.error} />
        {askForInfo && can(actions, "REQUEST_INFORMATION") && (
          <RequestInformationForm application={application} />
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_19rem]">
        <div className="min-w-0">
          <Tabs
            label="Application workspace"
            value={tab}
            onValueChange={(value) => {
              const next = new URLSearchParams(params);
              next.set("tab", value);
              setParams(next, { replace: true });
            }}
            items={[
              {
                value: "overview",
                label: "Overview",
                content: (
                  <div className="flex flex-col gap-6">
                    <Card title="Application">
                      <ApplicationSummary application={application} product={product} />
                    </Card>
                    <div className="grid gap-6 lg:grid-cols-2">
                      <NotesPanel applicationId={application.id} staff={staff} />
                      <Card title="Information requests">
                        <InformationRequestsPanel
                          applicationId={application.id}
                          canRespond={false}
                          emptyText="Nothing has been asked of the applicant."
                        />
                      </Card>
                    </div>
                    <Card title="Supporting documents">
                      <DocumentsPanel
                        applicationId={application.id}
                        canAdd={false}
                        canReview={allowed("application:request_info")}
                      />
                    </Card>
                  </div>
                ),
              },
              {
                value: "financial",
                label: "Financial profile",
                content: <FinancialTab applicantId={application.applicant_id} />,
              },
              {
                value: "affordability",
                label: "Affordability",
                content: (
                  <AffordabilityPanel applicationId={application.id} status={application.status} />
                ),
              },
              {
                value: "risk",
                label: "Credit risk",
                content: <RiskPanel applicationId={application.id} status={application.status} />,
              },
              {
                value: "decision",
                label: "Decision",
                content: (
                  <DecisionPanel application={application} evidence={evidence} staff={staff} />
                ),
              },
              {
                value: "loan",
                label: "Loan & repayments",
                content: <LoanTab application={application} />,
              },
              {
                value: "audit",
                label: "Audit",
                content: (
                  <div className="flex flex-col gap-6">
                    <Card title="Status history">
                      <StatusTimeline applicationId={application.id} staff={staff} />
                    </Card>
                    {allowed("audit:read") ? (
                      <AuditTrail entityType="loan_application" entityId={application.id} />
                    ) : (
                      <Notice tone="info">
                        The full audit log is available to administrators.
                      </Notice>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
        <SnapshotRail application={application} staff={staff} />
      </div>
    </>
  );
}

export function WorkspacePage() {
  const { applicationId = "" } = useParams();
  const application = useApplication(applicationId);
  return (
    <QueryView query={application} loadingLabel="Loading the application">
      {(data) => <Workspace application={data} />}
    </QueryView>
  );
}
