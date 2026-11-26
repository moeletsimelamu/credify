import type {
  ApplicationStatus,
  CommitmentType,
  DocumentType,
  EmploymentType,
  ExpenseCategory,
  IncomeFrequency,
  IncomeSourceType,
  LoanPurpose,
  LoanStatus,
  Role,
} from "@/api/types";

import { humanize } from "./format";

/** Options for a <select> from a list of enum values, with human labels. */
export function options<T extends string>(
  values: readonly T[],
  labels: Partial<Record<T, string>> = {},
): Array<{ value: T; label: string }> {
  return values.map((value) => ({ value, label: labels[value] ?? humanize(value) }));
}

export const ROLES: readonly Role[] = ["ADMIN", "LOAN_OFFICER", "CREDIT_ANALYST", "APPLICANT"];
export const ROLE_LABELS: Record<Role, string> = {
  ADMIN: "Administrator",
  LOAN_OFFICER: "Loan officer",
  CREDIT_ANALYST: "Credit analyst",
  APPLICANT: "Applicant",
};

export const APPLICATION_STATUSES: readonly ApplicationStatus[] = [
  "DRAFT",
  "SUBMITTED",
  "PENDING_REVIEW",
  "UNDER_REVIEW",
  "ADDITIONAL_INFO_REQUESTED",
  "RECOMMENDED_APPROVAL",
  "RECOMMENDED_REJECTION",
  "APPROVED",
  "DECLINED",
  "WITHDRAWN",
];
export const STATUS_LABELS: Partial<Record<ApplicationStatus, string>> = {
  ADDITIONAL_INFO_REQUESTED: "Information requested",
  RECOMMENDED_APPROVAL: "Recommended: approve",
  RECOMMENDED_REJECTION: "Recommended: decline",
};

export const LOAN_STATUSES: readonly LoanStatus[] = [
  "PENDING_DISBURSEMENT",
  "ACTIVE",
  "IN_ARREARS",
  "SETTLED",
  "CLOSED",
];

export const LOAN_PURPOSES: readonly LoanPurpose[] = [
  "DEBT_CONSOLIDATION",
  "HOME_IMPROVEMENT",
  "EDUCATION",
  "MEDICAL",
  "VEHICLE",
  "BUSINESS",
  "PERSONAL",
  "OTHER",
];

export const EMPLOYMENT_TYPES: readonly EmploymentType[] = [
  "PERMANENT",
  "CONTRACT",
  "SELF_EMPLOYED",
  "PART_TIME",
  "UNEMPLOYED",
  "RETIRED",
];

export const INCOME_SOURCES: readonly IncomeSourceType[] = [
  "SALARY",
  "SELF_EMPLOYMENT",
  "RENTAL",
  "PENSION",
  "GRANT",
  "OTHER",
];

export const INCOME_FREQUENCIES: readonly IncomeFrequency[] = [
  "WEEKLY",
  "FORTNIGHTLY",
  "MONTHLY",
  "ANNUALLY",
];

export const EXPENSE_CATEGORIES: readonly ExpenseCategory[] = [
  "HOUSING",
  "TRANSPORT",
  "FOOD",
  "UTILITIES",
  "EDUCATION",
  "INSURANCE",
  "MEDICAL",
  "DEPENDANTS",
  "OTHER",
];

export const COMMITMENT_TYPES: readonly CommitmentType[] = [
  "HOME_LOAN",
  "VEHICLE_FINANCE",
  "CREDIT_CARD",
  "PERSONAL_LOAN",
  "STORE_ACCOUNT",
  "STUDENT_LOAN",
  "OTHER",
];

export const DOCUMENT_TYPES: readonly DocumentType[] = [
  "PAYSLIP",
  "BANK_STATEMENT",
  "ID_DOCUMENT",
  "PROOF_OF_ADDRESS",
  "OTHER",
];

export function statusLabel(status: ApplicationStatus): string {
  return STATUS_LABELS[status] ?? humanize(status);
}
