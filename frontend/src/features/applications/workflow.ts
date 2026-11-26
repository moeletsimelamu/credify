import type { Action, ApplicationStatus } from "@/api/types";
import type { Step } from "@/shared/ui";

const STEPS = [
  { key: "draft", label: "Draft" },
  { key: "submitted", label: "Submitted" },
  { key: "review", label: "In review" },
  { key: "recommended", label: "Recommended" },
  { key: "decided", label: "Decided" },
] as const;

const STEP_OF: Record<ApplicationStatus, number> = {
  DRAFT: 0,
  SUBMITTED: 1,
  PENDING_REVIEW: 1,
  UNDER_REVIEW: 2,
  ADDITIONAL_INFO_REQUESTED: 2,
  RECOMMENDED_APPROVAL: 3,
  RECOMMENDED_REJECTION: 3,
  APPROVED: 4,
  DECLINED: 4,
  WITHDRAWN: 0,
};

/** Where an application sits in the lending workflow, for the stepper. */
export function workflowSteps(status: ApplicationStatus, hasLoan = false): Step[] {
  const current = STEP_OF[status];
  const finished = status === "APPROVED" || status === "DECLINED";
  const steps: Step[] = STEPS.map((step, index) => {
    if (status === "WITHDRAWN") {
      return {
        ...step,
        state: index === 0 ? "stopped" : "upcoming",
        label: index === 0 ? "Withdrawn" : step.label,
      };
    }
    if (index < current || (finished && index === current)) {
      return {
        ...step,
        state: status === "DECLINED" && index === current ? "stopped" : "done",
        label: index === 4 ? (status === "APPROVED" ? "Approved" : "Declined") : step.label,
      };
    }
    return { ...step, state: index === current ? "current" : "upcoming" };
  });
  if (status === "APPROVED") {
    steps.push({
      key: "loan",
      label: hasLoan ? "Loan opened" : "Awaiting disbursement",
      state: hasLoan ? "done" : "current",
    });
  }
  return steps;
}

/** Statuses a loan officer or analyst may need to act on. */
export const OPEN_STATUSES: ApplicationStatus[] = [
  "SUBMITTED",
  "PENDING_REVIEW",
  "UNDER_REVIEW",
  "ADDITIONAL_INFO_REQUESTED",
  "RECOMMENDED_APPROVAL",
  "RECOMMENDED_REJECTION",
];

/**
 * While the application is with the lender and undecided, staff may assign it and re-run its
 * assessments. Once a recommendation awaits a decision the evidence it cites must stay the
 * latest, so re-runs are blocked (backend ASSESSABLE / ASSIGNABLE).
 */
export const ASSESSABLE_STATUSES: ApplicationStatus[] = [
  "PENDING_REVIEW",
  "UNDER_REVIEW",
  "ADDITIONAL_INFO_REQUESTED",
];

export const DECIDED_STATUSES: ApplicationStatus[] = ["APPROVED", "DECLINED", "WITHDRAWN"];

export function can(actions: readonly Action[], action: Action): boolean {
  return actions.includes(action);
}
