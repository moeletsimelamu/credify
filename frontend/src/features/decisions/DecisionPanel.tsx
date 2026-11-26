import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import type {
  AffordabilityAssessment,
  ApplicationDetail,
  Decision,
  DecisionOutcome,
  Reason,
  RiskAssessment,
  StaffMember,
} from "@/api/types";
import {
  useDecide,
  useDecision,
  useReasonCodes,
  useRecommend,
  useRecommendations,
  useTransition,
} from "@/features/applications/api";
import { can } from "@/features/applications/workflow";
import { useCurrentUser } from "@/features/auth/useAuth";
import { applyServerErrors } from "@/shared/lib/forms";
import { formatDateTime, formatProbability, humanize } from "@/shared/lib/format";
import { optionalMoneyInput } from "@/shared/lib/validation";
import {
  Badge,
  Button,
  Card,
  CheckboxField,
  EmptyState,
  Fieldset,
  FormMessage,
  KeyValueList,
  MoneyText,
  Notice,
  QueryView,
  RiskBandBadge,
  StatusBadge,
  TextAreaField,
  TextField,
} from "@/shared/ui";

interface Evidence {
  affordability: AffordabilityAssessment | undefined;
  /** The latest *scored* risk assessment, which recommendations and decisions must cite. */
  risk: RiskAssessment | undefined;
}

function EvidenceSummary({ affordability, risk }: Evidence) {
  return (
    <div className="grid gap-3 rounded-card border border-line bg-sunken/50 p-4 text-sm sm:grid-cols-2">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted uppercase">
          Cites affordability
        </p>
        {affordability ? (
          <p className="mt-1 flex items-center gap-2">
            v{affordability.version} <StatusBadge status={affordability.outcome} />
          </p>
        ) : (
          <p className="mt-1 text-critical">No assessment yet — run one first.</p>
        )}
      </div>
      <div>
        <p className="text-xs font-medium tracking-wide text-muted uppercase">
          Cites risk score (advisory)
        </p>
        {risk ? (
          <p className="mt-1 flex items-center gap-2">
            v{risk.version} · {formatProbability(risk.probability_of_default)}{" "}
            <RiskBandBadge band={risk.risk_band} />
          </p>
        ) : (
          <p className="mt-1 text-muted">
            No score available — the decision rests on the other evidence.
          </p>
        )}
      </div>
    </div>
  );
}

// --- Recommendation (loan officer) -------------------------------------------------------

const recommendationSchema = z.object({
  recommendation: z.enum(["APPROVE", "REJECT"], "Choose a recommendation"),
  rationale: z
    .string()
    .trim()
    .min(1, "Explain your recommendation")
    .max(4000, "Keep it under 4,000 characters"),
});

function RecommendationForm({
  application,
  evidence,
}: {
  application: ApplicationDetail;
  evidence: Evidence;
}) {
  const recommend = useRecommend(application.id);
  const form = useForm<z.infer<typeof recommendationSchema>>({
    resolver: zodResolver(recommendationSchema),
    defaultValues: { recommendation: undefined, rationale: "" },
  });
  const { errors, isSubmitting } = form.formState;
  const submit = form.handleSubmit(async (values) => {
    if (!evidence.affordability) return;
    try {
      await recommend.mutateAsync({
        version: application.version,
        values: {
          ...values,
          affordability_assessment_id: evidence.affordability.id,
          credit_risk_assessment_id: evidence.risk?.id ?? null,
        },
      });
    } catch (error) {
      applyServerErrors(error, form.setError, ["recommendation", "rationale"]);
    }
  });
  return (
    <Card
      title="Recommend"
      description="Your recommendation goes to a credit analyst, who makes the decision."
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <EvidenceSummary {...evidence} />
        <Fieldset legend="Recommendation" error={errors.recommendation?.message}>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="APPROVE" {...form.register("recommendation")} /> Approve
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="REJECT" {...form.register("recommendation")} /> Decline
            </label>
          </div>
        </Fieldset>
        <TextAreaField
          label="Rationale"
          required
          rows={4}
          error={errors.rationale?.message}
          {...form.register("rationale")}
        />
        <FormMessage
          error={recommend.error && !Object.keys(errors).length ? recommend.error : undefined}
        />
        <div>
          <Button type="submit" loading={isSubmitting} disabled={!evidence.affordability}>
            Submit recommendation
          </Button>
        </div>
      </form>
    </Card>
  );
}

// --- Decision (credit analyst / administrator) -------------------------------------------

const decisionSchema = z.object({
  decision: z.enum(["APPROVED", "DECLINED"], "Choose approve or decline"),
  reason_codes: z
    .array(z.string())
    .min(1, "Choose at least one reason")
    .max(10, "Choose at most 10 reasons"),
  explanation: z
    .string()
    .trim()
    .min(1, "Explain the decision")
    .max(4000, "Keep it under 4,000 characters"),
  model_output_acknowledged: z
    .boolean()
    .refine((value) => value, "Confirm that you treated the model output as advisory"),
  approved_amount: optionalMoneyInput("Approved amount"),
  approved_term_months: z.string().regex(/^(\d{1,3})?$/, "Enter whole months"),
});
type DecisionValues = z.infer<typeof decisionSchema>;

function DecisionForm({
  application,
  evidence,
  reasons,
}: {
  application: ApplicationDetail;
  evidence: Evidence;
  reasons: Reason[];
}) {
  const decide = useDecide(application.id);
  const form = useForm<DecisionValues>({
    resolver: zodResolver(decisionSchema),
    defaultValues: {
      decision: undefined,
      reason_codes: [],
      explanation: "",
      model_output_acknowledged: false,
      approved_amount: "",
      approved_term_months: "",
    },
  });
  const outcome = useWatch({ control: form.control, name: "decision" }) as
    DecisionOutcome | undefined;
  const { errors, isSubmitting } = form.formState;
  const available = outcome ? reasons.filter((reason) => reason.outcome === outcome) : [];

  const submit = form.handleSubmit(async (values) => {
    if (!evidence.affordability) return;
    const approving = values.decision === "APPROVED";
    try {
      await decide.mutateAsync({
        version: application.version,
        values: {
          decision: values.decision,
          // Only codes that belong to the chosen outcome are sent.
          reason_codes: values.reason_codes.filter(
            (code) => reasons.find((r) => r.code === code)?.outcome === values.decision,
          ),
          explanation: values.explanation,
          model_output_acknowledged: values.model_output_acknowledged,
          affordability_assessment_id: evidence.affordability.id,
          credit_risk_assessment_id: evidence.risk?.id ?? null,
          approved_amount: approving && values.approved_amount ? values.approved_amount : null,
          approved_term_months:
            approving && values.approved_term_months ? Number(values.approved_term_months) : null,
        },
      });
    } catch (error) {
      applyServerErrors(error, form.setError, [
        "decision",
        "reason_codes",
        "explanation",
        "model_output_acknowledged",
        "approved_amount",
        "approved_term_months",
      ]);
    }
  });

  return (
    <Card
      title="Decide"
      description="The final, human decision. It is recorded with the evidence it cites."
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-5">
        <EvidenceSummary {...evidence} />
        <Fieldset legend="Decision" error={errors.decision?.message}>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="APPROVED" {...form.register("decision")} /> Approve
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" value="DECLINED" {...form.register("decision")} /> Decline
            </label>
          </div>
        </Fieldset>
        <Fieldset
          legend="Reasons"
          hint={
            outcome
              ? "Applicants see the plain-language text of each reason."
              : "Choose approve or decline to see the matching reasons."
          }
          error={errors.reason_codes?.message}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            {available.map((reason) => (
              <CheckboxField
                key={reason.code}
                value={reason.code}
                label={reason.label}
                hint={reason.applicant_text}
                {...form.register("reason_codes")}
              />
            ))}
          </div>
        </Fieldset>
        {outcome === "APPROVED" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Approved amount"
              inputMode="decimal"
              prefix="R"
              hint={
                <>
                  Leave blank to approve the requested{" "}
                  <MoneyText
                    amount={application.requested_amount}
                    currency={application.currency}
                  />
                  . A lower amount is a counter-offer.
                </>
              }
              error={errors.approved_amount?.message}
              {...form.register("approved_amount")}
            />
            <TextField
              label="Approved term (months)"
              inputMode="numeric"
              hint={`Leave blank for the requested ${application.requested_term_months} months.`}
              error={errors.approved_term_months?.message}
              {...form.register("approved_term_months")}
            />
          </div>
        )}
        <TextAreaField
          label="Explanation (internal)"
          required
          rows={4}
          error={errors.explanation?.message}
          {...form.register("explanation")}
        />
        <CheckboxField
          label="I treated the experimental model output as advisory and made this decision on the evidence."
          error={errors.model_output_acknowledged?.message}
          {...form.register("model_output_acknowledged")}
        />
        <FormMessage
          error={decide.error && !Object.keys(errors).length ? decide.error : undefined}
        />
        <div>
          <Button type="submit" loading={isSubmitting} disabled={!evidence.affordability}>
            Record decision
          </Button>
        </div>
      </form>
    </Card>
  );
}

const sendBackSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, "Say what needs another look")
    .max(2000, "Keep it under 2,000 characters"),
});

function SendBackForm({ application }: { application: ApplicationDetail }) {
  const sendBack = useTransition(application.id, "send-back");
  const form = useForm({ resolver: zodResolver(sendBackSchema), defaultValues: { reason: "" } });
  const submit = form.handleSubmit((values) =>
    sendBack.mutateAsync({ version: application.version, body: values }).then(() => form.reset()),
  );
  return (
    <Card title="Send back for more review">
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <TextAreaField
          label="Reason"
          required
          rows={2}
          error={form.formState.errors.reason?.message}
          {...form.register("reason")}
        />
        <FormMessage error={sendBack.error} />
        <div>
          <Button type="submit" variant="secondary" loading={form.formState.isSubmitting}>
            Send back
          </Button>
        </div>
      </form>
    </Card>
  );
}

function DecisionRecord({
  decision,
  currency,
  staff,
}: {
  decision: Decision;
  currency: string;
  staff: StaffMember[];
}) {
  const reasons = useReasonCodes().data;
  const name = (id: string) =>
    staff.find((member) => member.id === id)?.full_name ?? "Staff member";
  return (
    <Card title="Decision" actions={<StatusBadge status={decision.decision} />}>
      <div className="flex flex-col gap-4">
        <KeyValueList
          columns={3}
          items={[
            { label: "Decided by", value: name(decision.decided_by) },
            { label: "Decided", value: formatDateTime(decision.created_at) },
            {
              label: "Approved amount",
              value: <MoneyText amount={decision.approved_amount} currency={currency} />,
            },
            {
              label: "Approved term",
              value: decision.approved_term_months
                ? `${decision.approved_term_months} months`
                : "—",
            },
            {
              label: "Against the recommendation",
              value: decision.departs_from_recommendation ? (
                <Badge tone="warning">Yes</Badge>
              ) : (
                "No"
              ),
            },
            {
              label: "Against the model's guidance",
              value:
                decision.departs_from_model === null ? (
                  "No score was cited"
                ) : decision.departs_from_model ? (
                  <Badge tone="warning">Yes</Badge>
                ) : (
                  "No"
                ),
            },
          ]}
        />
        <div>
          <p className="text-sm font-medium">Reasons</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {decision.reason_codes.map((code) => (
              <li key={code}>
                <Badge>
                  {reasons?.find((reason) => reason.code === code)?.label ?? humanize(code)}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-sm font-medium">Explanation</p>
          <p className="mt-1 text-sm whitespace-pre-line">{decision.explanation}</p>
        </div>
      </div>
    </Card>
  );
}

export function DecisionPanel({
  application,
  evidence,
  staff,
}: {
  application: ApplicationDetail;
  evidence: Evidence;
  staff: StaffMember[];
}) {
  const user = useCurrentUser();
  const recommendations = useRecommendations(application.id);
  const decided = application.status === "APPROVED" || application.status === "DECLINED";
  const decision = useDecision<Decision>(application.id, decided);
  const reasons = useReasonCodes();
  const actions = application.available_actions;
  const name = (id: string) =>
    staff.find((member) => member.id === id)?.full_name ?? "Staff member";
  const latest = recommendations.data?.at(-1);
  const iRecommended = latest?.recommended_by === user.id;

  return (
    <div className="flex flex-col gap-6">
      {decided && (
        <QueryView query={decision} isEmpty={(data) => data === null} empty={null}>
          {(data) =>
            data && <DecisionRecord decision={data} currency={application.currency} staff={staff} />
          }
        </QueryView>
      )}

      <Card title="Recommendations">
        <QueryView
          query={recommendations}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState title="No recommendation yet">
              The assigned loan officer recommends approval or decline after review.
            </EmptyState>
          }
        >
          {(rows) => (
            <ul className="flex flex-col gap-3">
              {[...rows].reverse().map((row) => (
                <li key={row.id} className="rounded-card border border-line p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={row.recommendation === "APPROVE" ? "positive" : "critical"}>
                      {row.recommendation === "APPROVE"
                        ? "Recommends approval"
                        : "Recommends decline"}
                    </Badge>
                    <span className="text-xs text-muted">
                      {name(row.recommended_by)} · {formatDateTime(row.created_at)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm whitespace-pre-line">{row.rationale}</p>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </Card>

      {(can(actions, "RECOMMEND_APPROVAL") || can(actions, "RECOMMEND_REJECTION")) && (
        <RecommendationForm application={application} evidence={evidence} />
      )}

      {(can(actions, "APPROVE") || can(actions, "DECLINE")) &&
        (iRecommended ? (
          <Notice tone="info" title="Maker–checker">
            You made the recommendation, so a different analyst or administrator must decide.
          </Notice>
        ) : (
          <QueryView query={reasons} loadingLabel="Loading reason codes">
            {(data) => (
              <DecisionForm application={application} evidence={evidence} reasons={data} />
            )}
          </QueryView>
        ))}

      {can(actions, "SEND_BACK") && !iRecommended && <SendBackForm application={application} />}
    </div>
  );
}
