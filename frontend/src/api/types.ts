/**
 * Friendly names for the generated OpenAPI types (src/api/schema.d.ts, produced by
 * `npm run gen:api` from the backend's exported openapi.json). Never edit the generated file;
 * CI fails if it drifts from the backend.
 */
import type { components } from "./schema";

type S = components["schemas"];

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type Role = S["Role"];
export type CurrentUser = S["CurrentUserRead"];
export type TokenResponse = S["TokenResponse"];
export type User = S["UserRead"];
export type UserCreate = S["UserCreate"];
export type UserUpdate = S["UserUpdate"];
export type StaffMember = S["StaffMemberRead"];

export type Product = S["ProductRead"];
export type ProductCreate = S["ProductCreate"];
export type ProductUpdate = S["ProductUpdate"];
export type Currency = S["Currency"];

export type Applicant = S["ApplicantRead"];
export type ApplicantCreate = S["ApplicantCreate"];
export type FinancialProfile = S["FinancialProfileRead"];
export type Employment = S["EmploymentRead"];
export type EmploymentCreate = S["EmploymentCreate"];
export type EmploymentType = S["EmploymentType"];
export type Income = S["IncomeRead"];
export type IncomeCreate = S["IncomeCreate"];
export type IncomeSourceType = S["IncomeSourceType"];
export type IncomeFrequency = S["IncomeFrequency"];
export type Expense = S["ExpenseRead"];
export type ExpenseCreate = S["ExpenseCreate"];
export type ExpenseCategory = S["ExpenseCategory"];
export type Commitment = S["CommitmentRead"];
export type CommitmentCreate = S["CommitmentCreate"];
export type CommitmentType = S["CommitmentType"];

export type Application = S["ApplicationRead"];
export type ApplicationDetail = S["ApplicationDetail"];
export type ApplicationCreate = S["ApplicationCreate"];
export type ApplicationStatus = S["ApplicationStatus"];
export type Action = S["Action"];
export type LoanPurpose = S["LoanPurpose"];
export type StatusHistory = S["StatusHistoryRead"];
export type ReviewNote = S["ReviewNoteRead"];
export type InformationRequest = S["InformationRequestRead"];
export type ApplicationDocument = S["DocumentRead"];
export type DocumentType = S["DocumentType"];

export type AffordabilityAssessment = S["AffordabilityAssessmentRead"];
export type AffordabilityOutcome = S["Outcome"];
export type SchedulePreview = S["RepaymentSchedulePreview"];

export type RiskAssessment = S["RiskAssessmentRead"];
export type RiskBand = S["RiskBand"];
export type BureauReport = S["BureauReportRead"];
export type ModelVersion = S["ModelVersionRead"];

export type Recommendation = S["RecommendationRead"];
export type RecommendationCreate = S["RecommendationCreate"];
export type Decision = S["DecisionRead"];
export type ApplicantDecision = S["ApplicantDecisionRead"];
export type DecisionCreate = S["DecisionCreate"];
export type DecisionOutcome = S["DecisionOutcome"];
export type ReasonCode = S["ReasonCode"];
export type Reason = S["ReasonRead"];

export type LoanAccount = S["LoanAccountRead"];
export type LoanStatus = S["LoanStatus"];
export type LoanSchedule = S["LoanScheduleRead"];
export type EffectiveInstalment = S["EffectiveInstalmentRead"];
export type ScheduleVersion = S["ScheduleVersionRead"];
export type ScheduleVersionDetail = S["ScheduleVersionDetail"];
export type Transaction = S["TransactionRead"];
export type Charge = S["ChargeRead"];
export type Payment = S["PaymentRead"];
export type SettlementQuote = S["SettlementQuoteRead"];

export type Portfolio = S["PortfolioRead"];
export type CurrencyPortfolio = S["CurrencyPortfolioRead"];
export type AuditEvent = S["AuditEventRead"];

/** One factor in an exact logistic-regression explanation (credit_risk/explain.py). */
export interface Contribution {
  feature: string;
  label: string;
  value: string | number | null;
  display_value: string;
  log_odds: number;
  direction: "increases" | "decreases" | "neutral";
  text: string;
}

export interface Explanation {
  kind: string;
  base_log_odds: number;
  base_probability: number;
  log_odds: number;
  contributions: Contribution[];
  top_increasing: string[];
  top_decreasing: string[];
  caveat: string;
}

/** One affordability rule (affordability/domain/calculator.py RuleResult.as_dict). */
export interface RuleResult {
  rule_id: string;
  name: string;
  status: string;
  passed: boolean | null;
  effect: AffordabilityOutcome;
  threshold: string | null;
  actual: string | null;
  explanation: string;
}
