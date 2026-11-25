# Credify — Database Design

> **Status:** Approved 2026-11-18. Tables are created by Alembic migrations, phase by phase.

PostgreSQL 16. All schema changes go through Alembic; no manual DDL.

## 1. Conventions

| Concern | Convention |
|---|---|
| Primary keys | `id UUID` (generated in the application with `uuid7` for index locality) |
| Human references | Separate unique columns, e.g. `reference = 'CRD-2026-000123'`, `account_number = 'LA-000045'` |
| Money | `NUMERIC(18,2)` + sibling `currency CHAR(3)`; `CHECK (currency IN ('ZAR','USD','EUR','GBP'))` |
| Rates | `NUMERIC(9,6)` as a decimal fraction (`0.185000` = 18.5 % p.a.) |
| Ratios | `NUMERIC(10,4)` (`0.3512` = 35.12 %) |
| Probabilities | `NUMERIC(7,6)` with `CHECK (value BETWEEN 0 AND 1)` |
| Timestamps | `TIMESTAMPTZ`, written in UTC; `created_at` default `now()` |
| Calendar dates | `DATE` (due dates, employment start, value dates) |
| Enums | PostgreSQL `TEXT` + `CHECK` constraint (easier to migrate than native enums) mirrored by Python `StrEnum` |
| Optimistic locking | `version_id INTEGER NOT NULL DEFAULT 1` on mutable aggregates (`loan_applications`, `loan_accounts`) |
| Constraint names | SQLAlchemy naming convention (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`) |
| Append-only tables | Trigger `forbid_update_delete()` raises on `UPDATE`/`DELETE` |
| Soft vs hard delete | No hard deletes of financial or decision data. Mutable reference data uses `is_active` |

**Append-only tables:** `application_status_history`, `review_notes`, `affordability_assessments`, `credit_bureau_reports`, `credit_risk_assessments`, `recommendations`, `lending_decisions`, `loan_transactions`, `payment_allocations`, `audit_events`.

## 2. Entity–relationship overview

```
users ──1:0..1── applicants ──1:*── employment_records
  │                  │       ──1:*── income_records
  │                  │       ──1:*── expense_records
  │                  │       ──1:*── credit_commitments
  │                  └──1:*── loan_applications ──*:1── loan_products
  │                               ├──1:*── application_status_history
  │                               ├──1:*── review_notes
  │                               ├──1:*── information_requests
  │                               ├──1:*── document_references
  │                               ├──1:*── affordability_assessments
  │                               ├──1:*── credit_risk_assessments ──*:1── model_versions
  │                               ├──1:*── recommendations
  │                               ├──1:0..1── lending_decisions
  │                               └──1:0..1── loan_accounts
  │                                              ├──1:*── repayment_schedules ──1:*── schedule_instalments
  │                                              ├──1:*── loan_transactions ──1:*── payment_allocations
  │                                              └──1:*── loan_charges
  ├──1:*── refresh_tokens
  └──1:*── audit_events (actor)
```

## 3. Tables

### 3.1 Identity

**`users`**
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| email | CITEXT UNIQUE NOT NULL | |
| password_hash | TEXT NOT NULL | Argon2id encoded string |
| full_name | TEXT NOT NULL | |
| role | TEXT NOT NULL | `ADMIN`, `LOAN_OFFICER`, `CREDIT_ANALYST`, `APPLICANT` |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| failed_login_count | INTEGER NOT NULL DEFAULT 0 | |
| locked_until | TIMESTAMPTZ NULL | |
| last_login_at | TIMESTAMPTZ NULL | |
| created_at / updated_at | TIMESTAMPTZ | |

**`refresh_tokens`** — `id`, `user_id FK`, `family_id UUID`, `token_hash TEXT UNIQUE` (SHA-256), `expires_at`, `revoked_at NULL`, `replaced_by_id NULL`, `created_at`, `user_agent`. Index on `(user_id, revoked_at)`.

### 3.2 Products

**`loan_products`**
| Column | Type | Notes |
|---|---|---|
| id, code (UNIQUE), name, description | | e.g. `PERSONAL_UNSECURED` |
| currency | CHAR(3) | |
| min_amount, max_amount | NUMERIC(18,2) | `CHECK (0 < min_amount AND min_amount <= max_amount)` |
| min_term_months, max_term_months | SMALLINT | `CHECK (1 <= min_term_months AND min_term_months <= max_term_months AND max_term_months <= 120)` |
| annual_interest_rate | NUMERIC(9,6) | `CHECK (annual_interest_rate >= 0 AND annual_interest_rate < 1)` |
| initiation_fee | NUMERIC(18,2) | Capitalised into principal (see FINANCIAL_CALCULATIONS.md) |
| monthly_service_fee | NUMERIC(18,2) | Added to each instalment, not amortised |
| late_fee | NUMERIC(18,2) | Per missed instalment after grace |
| grace_period_days | SMALLINT | |
| early_settlement_fee_rate | NUMERIC(9,6) | Fraction of outstanding principal |
| max_dti, warn_dti | NUMERIC(10,4) | Affordability thresholds |
| min_residual_amount, min_residual_rate | NUMERIC(18,2), NUMERIC(10,4) | Affordability buffer |
| is_active | BOOLEAN | |
| created_at / updated_at | | Changes audited; applications snapshot the terms they were assessed under |

### 3.3 Applicants and financial profile

**`applicants`** — `id`, `user_id FK UNIQUE NULL`, `first_name`, `last_name`, `date_of_birth DATE` *(eligibility check ≥ 18 only — never a model feature)*, `email`, `phone`, `address_line1`, `address_line2`, `city`, `postal_code`, `country CHAR(2)`, `synthetic_reference` (UNIQUE, replaces a national ID), `created_by FK users`, timestamps.

**`employment_records`** — `id`, `applicant_id FK`, `employer_name NULL`, `job_title NULL`, `employment_type` (`PERMANENT`, `CONTRACT`, `SELF_EMPLOYED`, `PART_TIME`, `UNEMPLOYED`, `RETIRED`), `start_date NULL`, `end_date NULL`, `is_current`, `is_active`. `CHECK (end_date IS NULL OR end_date >= start_date)`, `CHECK (NOT (is_current AND end_date IS NOT NULL))`. Employer and start date are required by the API for every type except `UNEMPLOYED` and `RETIRED`.

**`income_records`**
| Column | Type | Notes |
|---|---|---|
| id, applicant_id FK | | |
| source_type | TEXT | `SALARY`, `SELF_EMPLOYMENT`, `RENTAL`, `PENSION`, `GRANT`, `OTHER` |
| frequency | TEXT | `WEEKLY`, `FORTNIGHTLY`, `MONTHLY`, `ANNUALLY` |
| gross_amount, net_amount | NUMERIC(18,2) | `CHECK (gross_amount > 0 AND net_amount > 0 AND net_amount <= gross_amount)` |
| currency | CHAR(3) | |
| verification_status | TEXT | `DECLARED`, `VERIFIED`, `REJECTED` (simulated) |
| is_active | BOOLEAN | |

**`expense_records`** — `id`, `applicant_id FK`, `category` (`HOUSING`, `TRANSPORT`, `FOOD`, `UTILITIES`, `EDUCATION`, `INSURANCE`, `MEDICAL`, `DEPENDANTS`, `OTHER`), `monthly_amount NUMERIC(18,2) CHECK (>= 0)`, `currency`, `is_active`.

**`credit_commitments`** — `id`, `applicant_id FK`, `commitment_type` (`HOME_LOAN`, `VEHICLE_FINANCE`, `CREDIT_CARD`, `PERSONAL_LOAN`, `STORE_ACCOUNT`, `STUDENT_LOAN`, `OTHER`), `lender_name`, `monthly_instalment CHECK (>= 0)`, `outstanding_balance CHECK (>= 0)`, `credit_limit NULL`, `currency`, `is_active`.

> Financial records describe the applicant's *current* position and are editable only while an application is `DRAFT` or `ADDITIONAL_INFO_REQUESTED`. History is preserved because every assessment stores a full JSONB snapshot of the inputs it used, and every edit writes an audit event with before/after values.

### 3.4 Applications

**`loan_applications`**
| Column | Type | Notes |
|---|---|---|
| id, reference (UNIQUE) | | |
| applicant_id FK, loan_product_id FK | | |
| requested_amount | NUMERIC(18,2) | `CHECK (> 0)` |
| currency | CHAR(3) | Must equal product currency (service-level check) |
| requested_term_months | SMALLINT | `CHECK (BETWEEN 1 AND 120)` |
| purpose | TEXT | `DEBT_CONSOLIDATION`, `HOME_IMPROVEMENT`, `EDUCATION`, `MEDICAL`, `VEHICLE`, `BUSINESS`, `PERSONAL`, `OTHER` |
| purpose_description | TEXT NULL | |
| status | TEXT | State machine values |
| product_terms_snapshot | JSONB NULL | Product rate/fees/thresholds frozen at submission |
| assigned_officer_id | FK users NULL | |
| submitted_at, decided_at | TIMESTAMPTZ NULL | |
| version_id | INTEGER | Optimistic lock |
| created_at / updated_at | | |

Indexes: `(status, submitted_at)`, `(applicant_id)`, `(assigned_officer_id, status)`, and the partial unique index `uq_loan_applications_one_open_per_applicant ON (applicant_id) WHERE status NOT IN ('APPROVED','DECLINED','WITHDRAWN')`.

`reference` is `CRD-<year>-<n>` where `n` comes from the sequence `loan_application_reference_seq`, zero-padded to six digits.

**`application_status_history`** *(append-only)* — `id`, `application_id FK`, `from_status NULL`, `to_status`, `actor_user_id FK NULL` (NULL = system), `reason TEXT NULL`, `created_at`.

**`review_notes`** *(append-only)* — `id`, `application_id`, `author_id`, `note_type` (`OFFICER_NOTE`, `ANALYST_ASSESSMENT`, `SYSTEM`), `body`, `created_at`.

**`information_requests`** — `id`, `application_id`, `requested_by`, `message`, `status` (`OPEN`, `RESPONDED`, `CANCELLED`), `response_text NULL`, `responded_at NULL`, `created_at`. `CHECK ((status = 'RESPONDED') = (response_text IS NOT NULL AND responded_at IS NOT NULL))`. Open requests are cancelled when the application is withdrawn.

**`document_references`** — `id`, `application_id`, `document_type` (`PAYSLIP`, `BANK_STATEMENT`, `ID_DOCUMENT`, `PROOF_OF_ADDRESS`, `OTHER`), `file_name`, `storage_key` (simulated reference, no real files in v1), `content_type`, `size_bytes`, `uploaded_by`, `uploaded_at`, `status` (`RECEIVED`, `ACCEPTED`, `REJECTED`).

### 3.5 Assessments, model registry, decisions

**`affordability_assessments`** *(append-only)*
| Column | Type | Notes |
|---|---|---|
| id, application_id FK | | |
| version | INTEGER | `UNIQUE (application_id, version)` (also serves as the application_id index); `CHECK (version >= 1)` |
| calculator_version | TEXT | e.g. `affordability-1.0.0` |
| run_reason | TEXT | `SUBMISSION`, `RESUBMISSION` (review resumed after more information), `STAFF_REQUEST` |
| inputs_snapshot | JSONB | Every income/expense/commitment row + product terms used |
| gross_monthly_income, net_monthly_income | NUMERIC(18,2) | |
| declared_living_expenses, expense_norm, applied_living_expenses | NUMERIC(18,2) | |
| existing_debt_instalments | NUMERIC(18,2) | |
| disposable_income | NUMERIC(18,2) | May be negative |
| proposed_instalment | NUMERIC(18,2) | |
| residual_income | NUMERIC(18,2) | May be negative |
| required_buffer | NUMERIC(18,2) | |
| debt_to_income_ratio, repayment_burden_ratio, new_loan_burden_ratio | NUMERIC(24,4) NULL | NULL when there is no counted income. Wider than the product thresholds because a ratio can be far above 1 and must still be stored exactly |
| outcome | TEXT | `AFFORDABLE`, `MARGINAL`, `UNAFFORDABLE`, `INSUFFICIENT_DATA` |
| rule_results | JSONB | Each rule: id, name, status (`PASSED`, `FAILED`, `NOT_EVALUATED`), effect, threshold, actual, explanation |
| currency | CHAR(3) | |
| created_by FK NULL, created_at | | NULL for automatic runs (submission, resubmission) |

**`model_versions`**
| Column | Type | Notes |
|---|---|---|
| id, name, version | | `UNIQUE (name, version)`, e.g. `credify-pd`, `1.0.0` |
| algorithm | TEXT | `logistic_regression`, `hist_gradient_boosting` |
| artefact_uri, artefact_sha256 | TEXT | Hash verified before load |
| feature_schema | JSONB | Ordered features, types, allowed ranges |
| training_dataset_sha256, dataset_generator_version, random_seed | | Reproducibility |
| metrics | JSONB | Validation and test metrics at the threshold, calibration error |
| decision_threshold | NUMERIC(7,6) | `CHECK (0 < threshold < 1)`; PD at or above it means "review carefully" |
| band_cutoffs | JSONB | PD → risk band mapping, with guidance text |
| explainer | JSONB | Coefficients, training means of the transformed features and the base log-odds, for exact explanations |
| libraries | JSONB | Library versions at training; the API refuses a scikit-learn mismatch |
| status | TEXT | `CANDIDATE`, `ACTIVE`, `RETIRED`; `CHECK`s tie `activated_at` and `retired_at` to the status |
| git_commit | TEXT | |
| trained_at, activated_at, retired_at, created_at | TIMESTAMPTZ | |

Partial unique index: `UNIQUE (name) WHERE status = 'ACTIVE'` — exactly one active model. `artefact_uri` is a plain relative path inside the read-only model directory; `artefact_sha256` must be 64 lowercase hex characters. Only `status` and its timestamps ever change, through the audited activation; registration happens only from the offline pipeline, so there is no `registered_by` column (the audit event records it, and a foreign key to `users` would tie the registry to demo data that a seed reset clears).

**`credit_bureau_reports`** *(append-only)* — `id`, `application_id FK UNIQUE` (pulled once per application), `applicant_id FK`, `provider` (`CHECK = 'SIMULATED'`), `simulator_version`, `missed_payments_12m` (0–12), `months_since_delinquency NULL` (0–600; NULL = never delinquent), `created_at`. A deterministic simulation: no real bureau is contacted.

**`credit_risk_assessments`** *(append-only)* — `id`, `application_id`, `version` (`UNIQUE (application_id, version)`), `run_reason` (`SUBMISSION`, `RESUBMISSION`, `STAFF_REQUEST`), `status` (`SCORED`, `FAILED`), `model_version_id FK NULL` (NULL when no model was active), `affordability_assessment_id FK NULL`, `bureau_report_id FK NULL`, `feature_snapshot JSONB` (the 17 exact model inputs, their order, the schema version and their sources), `probability_of_default NUMERIC(7,6)`, `risk_band` (`A`–`E`), `decision_threshold`, `above_threshold`, `explanation JSONB` (ranked contributions, base and total log-odds, caveat), `failure_reason`, `requested_by NULL` (NULL for automatic runs), `created_at`. `CHECK`s: a `SCORED` row has every result field; a `FAILED` row has a reason.

**`recommendations`** *(append-only)* — `id`, `application_id`, `recommended_by`, `recommendation` (`APPROVE`, `REJECT`), `rationale` (`CHECK` ≥ 20 non-blank characters), `affordability_assessment_id FK` (must be the latest version when recorded), `credit_risk_assessment_id FK NULL` (the latest *scored* risk assessment, required when one exists), `created_at`. Index on `(application_id, created_at)`; the latest row is the one a decision responds to.

**`lending_decisions`** *(append-only)*
| Column | Type | Notes |
|---|---|---|
| id, application_id | | `UNIQUE (application_id)` — one final decision |
| decision | TEXT | `APPROVED`, `DECLINED` |
| decided_by FK users | | `CHECK`-equivalent service rule: ≠ recommendation author |
| recommendation_id FK | | Recommendation the decision responds to |
| affordability_assessment_id | FK | Evidence actually reviewed (must be the latest version) |
| reason_codes | TEXT[] | `CHECK (cardinality >= 1)`. From the controlled list in `decisions/domain/reason_codes.py`; each code supports either an approval or a decline, and the guard rejects codes that do not match the decision |
| explanation | TEXT | Human-written, required, internal (applicants see the codes' plain-language text instead) |
| model_acknowledged | BOOLEAN | `CHECK (model_acknowledged)` |
| departs_from_recommendation | BOOLEAN | The decision differs from the recommendation it responds to |
| approved_amount, approved_term_months | NULL | Set exactly when `decision = 'APPROVED'` (`CHECK`). A counter-offer may lower the amount or change the term, must cite `COUNTER_OFFER`, and may not cost more per month than the assessed instalment |
| created_at | | |

| credit_risk_assessment_id | FK NULL | The latest scored risk assessment, cited (required when one exists) |
| departs_from_model | BOOLEAN NULL | Approving an application flagged "review carefully" (PD ≥ threshold), or declining one that is not. NULL when no scored assessment existed |

### 3.6 Loans and repayments

**`loan_accounts`**
| Column | Type | Notes |
|---|---|---|
| id, account_number (UNIQUE) | | `LA-<n>` from `loan_account_number_seq`, six digits |
| application_id FK UNIQUE, decision_id FK UNIQUE | | One account per approval (this is what makes disbursement idempotent) |
| applicant_id FK | | Indexed; used for applicant scoping |
| approved_amount, initiation_fee | NUMERIC(18,2) | From the decision and the frozen product terms |
| principal | NUMERIC(18,2) | `CHECK (principal = approved_amount + initiation_fee)` |
| currency | CHAR(3) | |
| annual_interest_rate | NUMERIC(9,6) | Snapshot |
| term_months | SMALLINT | Contractual term (a recast shortens the schedule, not this) |
| instalment_amount | NUMERIC(18,2) | Amortising instalment, excl. service fee |
| monthly_service_fee, late_fee | NUMERIC(18,2) | Snapshot |
| grace_period_days | SMALLINT | Snapshot |
| early_settlement_fee_rate | NUMERIC(9,6) | Snapshot |
| disbursed_on, first_due_date, maturity_date | DATE | `maturity_date` moves earlier after a prepayment recast |
| status | TEXT | `PENDING_DISBURSEMENT` (reserved; simulated disbursement is immediate), `ACTIVE`, `IN_ARREARS`, `SETTLED`, `CLOSED` |
| outstanding_principal, arrears_amount | NUMERIC(18,2) | Cached; reconciled from `loan_transactions` |
| days_past_due | INTEGER | Cached |
| position_as_of | DATE | The date the cached figures were calculated for |
| version_id | INTEGER | Optimistic lock |
| settled_on | DATE NULL | `CHECK ((status = 'SETTLED') = (settled_on IS NOT NULL))` |

**`repayment_schedules`** — `id`, `loan_account_id`, `version` (`UNIQUE (loan_account_id, version)`), `reason` (`ORIGINATION`, `PREPAYMENT_RECAST`), `engine_version`, `first_instalment_number`, `is_current BOOLEAN`, `transaction_id FK NULL` (the repayment whose prepayment caused the recast), `created_at`. Partial unique index: one current schedule per loan.

The **effective schedule** is built version by version: each version replaces every instalment from its `first_instalment_number` on. A recast only ever replaces instalments nothing has been paid against, so earlier versions' rows stay valid and history is never rewritten.

**`schedule_instalments`** — `id`, `schedule_id`, `instalment_number` (`UNIQUE (schedule_id, instalment_number)`), `due_date`, `opening_balance`, `interest_due`, `principal_due`, `service_fee_due`, `total_due`, `closing_balance`, `interest_paid`, `principal_paid`, `service_fee_paid`, `status` (`SCHEDULED`, `DUE`, `PARTIALLY_PAID`, `PAID`, `OVERDUE`, `CANCELLED`). `CHECK (interest_paid <= interest_due AND principal_paid <= principal_due AND service_fee_paid <= service_fee_due)`, `CHECK (total_due = interest_due + principal_due + service_fee_due)`, `CHECK (closing_balance = opening_balance - principal_due)`. `CANCELLED` marks the remaining instalments of a loan settled early: their principal was repaid by the settlement and their scheduled interest and fees are no longer owed.

**`loan_charges`** — `id`, `loan_account_id`, `schedule_instalment_id NULL`, `charge_type` (`LATE_FEE`, `EARLY_SETTLEMENT_FEE`), `amount`, `amount_paid`, `charged_on`, `created_at`. `UNIQUE (schedule_instalment_id, charge_type)` prevents double late fees; `CHECK ((charge_type = 'LATE_FEE') = (schedule_instalment_id IS NOT NULL))`; `CHECK (0 <= amount_paid <= amount)`.

**`loan_transactions`** *(append-only — the account statement)* — `id`, `loan_account_id`, `transaction_type` (`DISBURSEMENT`, `REPAYMENT`, `CHARGE`, `SETTLEMENT`, `REVERSAL`), `amount CHECK (> 0)`, `currency`, `value_date DATE`, `idempotency_key UNIQUE NULL`, `channel` (`SIMULATED`), `reference`, `reverses_transaction_id NULL`, `recorded_by NULL` (NULL = the arrears job), `balance_after NUMERIC(18,2)`, `created_at`. Index on `(loan_account_id, value_date)`.

- `balance_after` is what the borrower owes as at the value date once the transaction is applied: outstanding principal + interest and service fees due by then + unpaid charges. It is computed before the row is written, because the row can never be updated.
- `DISBURSEMENT` is for the principal financed (the capitalised initiation fee is named in its reference). Late fees and the early settlement fee each appear as a `CHARGE`.
- `REVERSAL` is reserved; v1 has no reversal endpoint.

**`payment_allocations`** *(append-only)* — `id`, `transaction_id FK`, `schedule_instalment_id NULL`, `loan_charge_id NULL`, `component` (`CHARGE`, `SERVICE_FEE`, `INTEREST`, `PRINCIPAL`, `PREPAYMENT`), `amount CHECK (> 0)`. `CHECK`s tie the target to the component: a `CHARGE` names a charge, `SERVICE_FEE`/`INTEREST`/`PRINCIPAL` name an instalment, `PREPAYMENT` names neither. Sum of allocations = transaction amount (verified by `loans.service.reconcile`, which the arrears job runs on every account, and by tests).

### 3.7 Audit

**`audit_events`** *(append-only)* — `id`, `occurred_at`, `actor_user_id NULL`, `actor_role`, `action` (e.g. `application.status_changed`, `decision.recorded`, `income.updated`, `auth.login_failed`), `entity_type`, `entity_id`, `request_id`, `ip_address INET NULL`, `changes JSONB` (before/after), `context JSONB` (non-diff details, e.g. a failure reason). Indexes on `(entity_type, entity_id, occurred_at)` and `(actor_user_id, occurred_at)`.

## 4. Concurrency and atomicity

- Every use case is one transaction: state change + history row + audit event commit together or not at all.
- Recording a repayment: `SELECT … FOR UPDATE` on the loan account row → allocate → insert transaction + allocations → update instalments, cached balances, status → audit → commit.
- Duplicate submissions are rejected by `idempotency_key` uniqueness; the API returns the original result for a replayed key.
- Status transitions use optimistic locking (`version_id`); conflicts return HTTP 409.

## 5. Seed data

Two distinct synthetic datasets (never mixed):

1. **Demo seed** (`backend/scripts/seed_demo_data.py`, deterministic for a given `--seed` and `--as-of`): 5 staff users (admin, two loan officers, two credit analysts), 3 loan products, 40 applicants with applications in every status except the transient `SUBMITTED`, and 15 loan accounts with on-time, late (with late fees), in-arrears (three ageing buckets), prepaid (recast schedule) and settled histories. It drives the same service functions as the API with a simulated clock, so every row obeys the business rules and has an audit trail. It refuses to run in production or on a database that already has users (unless `--reset`).
2. **ML training dataset** — generated offline by `ml/`; see `docs/ML_STRATEGY.md`.

Demo credentials are generated at seed time and written to a git-ignored local file (`.local/demo-credentials.txt` under Compose), never committed. Every demo address uses the reserved `credify-demo.example` domain.
