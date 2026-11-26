import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams, useSearchParams } from "react-router";
import { z } from "zod";

import type { ApplicantDecision, ApplicationDetail, LoanPurpose } from "@/api/types";
import {
  useApplication,
  useApplicationLoan,
  useApplicationSchedule,
  useDecision,
  useProducts,
  useTransition,
  useUpdateApplication,
} from "@/features/applications/api";
import {
  ApplicationSummary,
  DocumentsPanel,
  InformationRequestsPanel,
  StatusTimeline,
} from "@/features/applications/components";
import { can, workflowSteps } from "@/features/applications/workflow";
import { SchedulePreviewView } from "@/features/loans/ScheduleTables";
import { applyServerErrors, usePageTitle } from "@/shared/lib/forms";
import { LOAN_PURPOSES, options, statusLabel } from "@/shared/lib/labels";
import { moneyInput, optionalText } from "@/shared/lib/validation";
import {
  Button,
  Card,
  ConfirmDialog,
  FormMessage,
  MoneyText,
  Notice,
  PageHeader,
  QueryView,
  SelectField,
  StatusBadge,
  Stepper,
  TextAreaField,
  TextField,
} from "@/shared/ui";

const draftSchema = z.object({
  requested_amount: moneyInput("Amount"),
  requested_term_months: z.string().regex(/^\d{1,3}$/, "Enter the term in whole months"),
  purpose: z.string().min(1, "Choose what the loan is for"),
  purpose_description: optionalText(500),
});
type DraftValues = z.infer<typeof draftSchema>;

function DraftEditor({ application }: { application: ApplicationDetail }) {
  const update = useUpdateApplication(application.id);
  const [saved, setSaved] = useState(false);
  const form = useForm<DraftValues>({
    resolver: zodResolver(draftSchema),
    defaultValues: {
      requested_amount: application.requested_amount,
      requested_term_months: String(application.requested_term_months),
      purpose: application.purpose,
      purpose_description: application.purpose_description ?? "",
    },
  });
  const { errors, isSubmitting, isDirty } = form.formState;
  const submit = form.handleSubmit(async (values) => {
    setSaved(false);
    try {
      await update.mutateAsync({
        version: application.version,
        values: {
          requested_amount: values.requested_amount,
          requested_term_months: Number(values.requested_term_months),
          purpose: values.purpose as LoanPurpose,
          purpose_description: values.purpose_description || null,
        },
      });
      form.reset(values);
      setSaved(true);
    } catch (error) {
      applyServerErrors(error, form.setError, [
        "requested_amount",
        "requested_term_months",
        "purpose",
        "purpose_description",
      ]);
    }
  });
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label="Amount"
          inputMode="decimal"
          prefix="R"
          required
          error={errors.requested_amount?.message}
          {...form.register("requested_amount")}
        />
        <TextField
          label="Term (months)"
          inputMode="numeric"
          required
          error={errors.requested_term_months?.message}
          {...form.register("requested_term_months")}
        />
        <SelectField
          label="Purpose"
          required
          options={options(LOAN_PURPOSES)}
          error={errors.purpose?.message}
          {...form.register("purpose")}
        />
      </div>
      <TextAreaField
        label="Details (optional)"
        rows={2}
        error={errors.purpose_description?.message}
        {...form.register("purpose_description")}
      />
      <FormMessage
        error={update.error && !Object.keys(errors).length ? update.error : undefined}
        success={saved && !isDirty ? "Draft saved." : undefined}
      />
      <div>
        <Button type="submit" variant="secondary" loading={isSubmitting} disabled={!isDirty}>
          Save draft
        </Button>
      </div>
    </form>
  );
}

function DecisionOutcome({ applicationId, currency }: { applicationId: string; currency: string }) {
  const decision = useDecision<ApplicantDecision>(applicationId);
  return (
    <QueryView query={decision} isEmpty={(data) => data === null} empty={null}>
      {(data) =>
        data && (
          <Card
            title={
              data.decision === "APPROVED"
                ? "Your application was approved"
                : "Your application was declined"
            }
          >
            {data.decision === "APPROVED" && (
              <p className="mb-3 text-sm">
                Approved for{" "}
                <MoneyText
                  amount={data.approved_amount}
                  currency={currency}
                  className="font-semibold"
                />{" "}
                over {data.approved_term_months} months.
              </p>
            )}
            <p className="text-sm font-medium">Reasons</p>
            <ul className="mt-1 list-disc pl-5 text-sm">
              {data.reasons.map((reason) => (
                <li key={reason.code}>{reason.applicant_text}</li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted">
              This decision was made by a person. Credify is a simulation: no real credit has been
              granted or refused.
            </p>
          </Card>
        )
      }
    </QueryView>
  );
}

function ApplicationView({ application }: { application: ApplicationDetail }) {
  const [params] = useSearchParams();
  const products = useProducts(true);
  const product = products.data?.items.find((item) => item.id === application.loan_product_id);
  const decided = application.status === "APPROVED" || application.status === "DECLINED";
  const loan = useApplicationLoan(application.id, application.status === "APPROVED");
  const schedule = useApplicationSchedule(application.id);
  const submit = useTransition(application.id, "submit");
  const resume = useTransition(application.id, "resume-review");
  const withdraw = useTransition(application.id, "withdraw");
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);
  const actions = application.available_actions;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/portal" className="hover:underline">
            ← Overview
          </Link>
        }
        title={<span className="figure">{application.reference}</span>}
        actions={
          <StatusBadge status={application.status} label={statusLabel(application.status)} />
        }
      />
      <div className="mb-6">
        <Stepper
          label="Application progress"
          steps={workflowSteps(application.status, Boolean(loan.data))}
        />
      </div>

      <div className="flex flex-col gap-6">
        {params.get("created") && application.status === "DRAFT" && (
          <Notice tone="positive" role="status" title="Draft saved">
            Check the details and the repayment schedule below, then submit your application.
          </Notice>
        )}
        {application.status === "ADDITIONAL_INFO_REQUESTED" && (
          <Notice tone="warning" title="The lender needs more information">
            Reply to the request below. You can also update your details and finances now; when
            you're done, send the application back for review.
          </Notice>
        )}
        {loan.data && (
          <Notice tone="positive" title="Your loan is open">
            Account <span className="figure">{loan.data.account_number}</span>.{" "}
            <Link
              to={`/portal/loans/${loan.data.id}`}
              className="font-medium text-accent underline-offset-2 hover:underline"
            >
              View your loan and make payments
            </Link>
          </Notice>
        )}

        {decided && (
          <DecisionOutcome applicationId={application.id} currency={application.currency} />
        )}

        <Card
          title="Application"
          actions={
            <>
              {can(actions, "SUBMIT") && (
                <Button
                  onClick={() => submit.mutate({ version: application.version })}
                  loading={submit.isPending}
                >
                  Submit application
                </Button>
              )}
              {can(actions, "RESUME_REVIEW") && (
                <Button
                  onClick={() => resume.mutate({ version: application.version })}
                  loading={resume.isPending}
                >
                  Send back for review
                </Button>
              )}
              {can(actions, "WITHDRAW") && (
                <Button variant="secondary" onClick={() => setConfirmWithdraw(true)}>
                  Withdraw
                </Button>
              )}
            </>
          }
        >
          <div className="flex flex-col gap-4">
            <FormMessage error={submit.error ?? resume.error} />
            {application.status === "DRAFT" ? (
              <DraftEditor application={application} />
            ) : (
              <ApplicationSummary application={application} product={product} />
            )}
          </div>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Information requests">
            <InformationRequestsPanel
              applicationId={application.id}
              canRespond={application.status === "ADDITIONAL_INFO_REQUESTED"}
              emptyText="If the lender needs anything else, their questions appear here."
            />
          </Card>
          <Card title="Progress">
            <StatusTimeline applicationId={application.id} />
          </Card>
        </div>

        <Card title="Supporting documents">
          <DocumentsPanel
            applicationId={application.id}
            canAdd={!decided && application.status !== "WITHDRAWN"}
            canReview={false}
          />
        </Card>

        {!decided && application.status !== "WITHDRAWN" && (
          <Card
            title="Repayment schedule"
            description="Illustrative, based on the amount and term you asked for."
          >
            <QueryView query={schedule} loadingLabel="Calculating the schedule">
              {(preview) => <SchedulePreviewView preview={preview} />}
            </QueryView>
          </Card>
        )}
      </div>

      <ConfirmDialog
        open={confirmWithdraw}
        onOpenChange={setConfirmWithdraw}
        title="Withdraw this application?"
        confirmLabel="Withdraw application"
        danger
        pending={withdraw.isPending}
        error={withdraw.error ? <FormMessage error={withdraw.error} /> : undefined}
        onConfirm={() =>
          withdraw.mutate(
            { version: application.version, body: {} },
            { onSuccess: () => setConfirmWithdraw(false) },
          )
        }
      >
        The lender will stop reviewing it. You can't reopen a withdrawn application, but you can
        start a new one.
      </ConfirmDialog>
    </>
  );
}

export function MyApplicationPage() {
  const { applicationId = "" } = useParams();
  usePageTitle("Application");
  const application = useApplication(applicationId);
  return (
    <QueryView query={application} loadingLabel="Loading the application">
      {(data) => <ApplicationView application={data} />}
    </QueryView>
  );
}
