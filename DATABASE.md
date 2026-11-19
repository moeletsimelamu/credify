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

**Append-only tables:** `application_status_history`, `review_notes`, `affordability_assessments`, `credit_risk_assessments`, `recommendations`, `lending_decisions`, `loan_transactions`, `payment_allocations`, `audit_events`.

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

**`employment_records`** — `id`, `applicant_id FK`, `employer_name`, `job_title`, `employment_type` (`PERMANENT`, `CONTRACT`, `SELF_EMPLOYED`, `PART_TIME`, `UNEMPLOYED`, `RETIRED`), `start_date`, `end_date NULL`, `is_current`. `CHECK (end_date IS NULL OR end_date >= start_date)`.

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

Indexes: `(status, submitted_at)`, `(applicant_id)`, `(assigned_officer_id, status)`.

**`application_status_history`** *(append-only)* — `id`, `application_id FK`, `from_status NULL`, `to_status`, `actor_user_id FK NULL` (NULL = system), `reason TEXT NULL`, `created_at`.

**`review_notes`** *(append-only)* — `id`, `application_id`, `author_id`, `note_type` (`OFFICER_NOTE`, `ANALYST_ASSESSMENT`, `SYSTEM`), `body`, `created_at`.

**`information_requests`** — `id`, `application_id`, `requested_by`, `message`, `status` (`OPEN`, `RESPONDED`, `CANCELLED`), `response_text NULL`, `responded_at NULL`, `created_at`.

**`document_references`** — `id`, `application_id`, `document_type` (`PAYSLIP`, `BANK_STATEMENT`, `ID_DOCUMENT`, `PROOF_OF_ADDRESS`, `OTHER`), `file_name`, `storage_key` (simulated reference, no real files in v1), `content_type`, `size_bytes`, `uploaded_by`, `uploaded_at`, `status` (`RECEIVED`, `ACCEPTED`, `REJECTED`).

### 3.5 Assessments, model registry, decisions

**`affordability_assessments`** *(append-only)*
| Column | Type | Notes |
|---|---|---|
| id, application_id FK | | |
| version | INTEGER | `UNIQUE (application_id, version)` |
| calculator_version | TEXT | e.g. `affordability-1.0.0` |
| inputs_snapshot | JSONB | Every income/expense/commitment row + product terms used |
| gross_monthly_income, net_monthly_income | NUMERIC(18,2) | |
| declared_living_expenses, expense_norm, applied_living_expenses | NUMERIC(18,2) | |
| existing_debt_instalments | NUMERIC(18,2) | |
| disposable_income | NUMERIC(18,2) | May be negative |
| proposed_instalment | NUMERIC(18,2) | |
| residual_income | NUMERIC(18,2) | May be negative |
| required_buffer | NUMERIC(18,2) | |
| debt_to_income_ratio, repayment_burden_ratio | NUMERIC(10,4) | |
| outcome | TEXT | `AFFORDABLE`, `MARGINAL`, `UNAFFORDABLE`, `INSUFFICIENT_DATA` |
| rule_results | JSONB | Each rule: id, threshold, actual, passed |
| currency | CHAR(3) | |
| created_by FK NULL, created_at | | |

**`model_versions`**
| Column | Type | Notes |
|---|---|---|
| id, name, version | | `UNIQUE (name, version)`, e.g. `credify-pd`, `1.0.0` |
| algorithm | TEXT | `logistic_regression`, `hist_gradient_boosting` |
| artefact_uri, artefact_sha256 | TEXT | Hash verified before load |
| feature_schema | JSONB | Ordered features, types, allowed ranges |
| training_dataset_sha256, dataset_generator_version, random_seed | | Reproducibility |
| metrics | JSONB | Test-set metrics, threshold table, fairness metrics |
| decision_threshold | NUMERIC(7,6) | |
| band_cutoffs | JSONB | PD → risk band mapping |
| status | TEXT | `CANDIDATE`, `ACTIVE`, `RETIRED` |
| git_commit | TEXT | |
| trained_at, activated_at, retired_at | TIMESTAMPTZ | |

Partial unique index: `UNIQUE (name) WHERE status = 'ACTIVE'` — exactly one active model.

**`credit_risk_assessments`** *(append-only)* — `id`, `application_id`, `version` (`UNIQUE (application_id, version)`), `model_version_id FK`, `feature_snapshot JSONB` (exact model inputs), `probability_of_default NUMERIC(7,6)`, `risk_band` (`A`–`E`), `explanation JSONB` (per-feature contributions, base value), `status` (`SCORED`, `FAILED`), `failure_reason NULL`, `requested_by NULL`, `created_at`.

**`recommendations`** *(append-only)* — `id`, `application_id`, `recommended_by`, `recommendation` (`APPROVE`, `REJECT`), `rationale`, `affordability_assessment_id FK`, `credit_risk_assessment_id FK NULL`, `created_at`.

**`lending_decisions`** *(append-only)*
| Column | Type | Notes |
|---|---|---|
| id, application_id | | `UNIQUE (application_id)` — one final decision |
| decision | TEXT | `APPROVED`, `DECLINED` |
| decided_by FK users | | `CHECK`-equivalent service rule: ≠ recommendation author |
| recommendation_id FK | | Recommendation the decision responds to |
| affordability_assessment_id, credit_risk_assessment_id | FK | Evidence actually reviewed |
| reason_codes | TEXT[] | From a controlled list (e.g. `AFFORDABILITY_SHORTFALL`, `HIGH_DTI`, `ADVERSE_REPAYMENT_HISTORY`, `INSUFFICIENT_INCOME_EVIDENCE`, `MEETS_CRITERIA`) |
| explanation | TEXT | Human-written, required |
| model_acknowledged | BOOLEAN | `CHECK (model_acknowledged)` |
| departs_from_model | BOOLEAN | Decision differs from model band guidance |
| approved_amount, approved_term_months | NULL | May be lower than requested (counter-offer) |
| created_at | | |

### 3.6 Loans and repayments

**`loan_accounts`**
| Column | Type | Notes |
|---|---|---|
| id, account_number (UNIQUE) | | |
| application_id FK UNIQUE | | |
| principal | NUMERIC(18,2) | Approved amount + capitalised initiation fee |
| currency | CHAR(3) | |
| annual_interest_rate | NUMERIC(9,6) | Snapshot |
| term_months | SMALLINT | |
| instalment_amount | NUMERIC(18,2) | Amortising instalment, excl. service fee |
| monthly_service_fee | NUMERIC(18,2) | |
| disbursed_on, first_due_date, maturity_date | DATE | |
| status | TEXT | `PENDING_DISBURSEMENT`, `ACTIVE`, `IN_ARREARS`, `SETTLED`, `CLOSED` |
| outstanding_principal, arrears_amount | NUMERIC(18,2) | Cached; reconciled from `loan_transactions` |
| days_past_due | INTEGER | Cached |
| version_id | INTEGER | Optimistic lock |
| settled_on | DATE NULL | |

**`repayment_schedules`** — `id`, `loan_account_id`, `version` (`UNIQUE (loan_account_id, version)`), `reason` (`ORIGINATION`, `PREPAYMENT_RECAST`), `is_current BOOLEAN`, `created_at`. Partial unique index: one current schedule per loan.

**`schedule_instalments`** — `id`, `schedule_id`, `instalment_number` (`UNIQUE (schedule_id, instalment_number)`), `due_date`, `opening_balance`, `interest_due`, `principal_due`, `service_fee_due`, `total_due`, `closing_balance`, `interest_paid`, `principal_paid`, `service_fee_paid`, `status` (`SCHEDULED`, `DUE`, `PARTIALLY_PAID`, `PAID`, `OVERDUE`). `CHECK (interest_paid <= interest_due AND principal_paid <= principal_due AND service_fee_paid <= service_fee_due)`.

**`loan_charges`** — `id`, `loan_account_id`, `schedule_instalment_id NULL`, `charge_type` (`LATE_FEE`, `EARLY_SETTLEMENT_FEE`), `amount`, `amount_paid`, `charged_on`, `created_at`. `UNIQUE (schedule_instalment_id, charge_type)` prevents double late fees.

**`loan_transactions`** *(append-only — the account statement)* — `id`, `loan_account_id`, `transaction_type` (`DISBURSEMENT`, `REPAYMENT`, `CHARGE`, `SETTLEMENT`, `REVERSAL`), `amount CHECK (> 0)`, `currency`, `value_date DATE`, `idempotency_key UNIQUE NULL`, `channel` (`SIMULATED`), `reference`, `reverses_transaction_id NULL`, `recorded_by NULL`, `balance_after NUMERIC(18,2)`, `created_at`.

**`payment_allocations`** *(append-only)* — `id`, `transaction_id FK`, `schedule_instalment_id NULL`, `loan_charge_id NULL`, `component` (`CHARGE`, `SERVICE_FEE`, `INTEREST`, `PRINCIPAL`, `PREPAYMENT`), `amount CHECK (> 0)`. Sum of allocations = transaction amount (verified in service + tested).

### 3.7 Audit

**`audit_events`** *(append-only)* — `id`, `occurred_at`, `actor_user_id NULL`, `actor_role`, `action` (e.g. `application.status_changed`, `decision.recorded`, `income.updated`, `auth.login_failed`), `entity_type`, `entity_id`, `request_id`, `ip_address INET NULL`, `changes JSONB` (before/after), `context JSONB` (non-diff details, e.g. a failure reason). Indexes on `(entity_type, entity_id, occurred_at)` and `(actor_user_id, occurred_at)`.

## 4. Concurrency and atomicity

- Every use case is one transaction: state change + history row + audit event commit together or not at all.
- Recording a repayment: `SELECT … FOR UPDATE` on the loan account row → allocate → insert transaction + allocations → update instalments, cached balances, status → audit → commit.
- Duplicate submissions are rejected by `idempotency_key` uniqueness; the API returns the original result for a replayed key.
- Status transitions use optimistic locking (`version_id`); conflicts return HTTP 409.

## 5. Seed data

Two distinct synthetic datasets (never mixed):

1. **Demo seed** (`backend/scripts/seed_demo_data.py`, deterministic seed): staff users for each role, 3 loan products, ~40 applicants, applications across every status, ~15 loan accounts with repayment histories including on-time, late and settled loans — so every screen has realistic content.
2. **ML training dataset** — generated offline by `ml/`; see `docs/ML_STRATEGY.md`.

Demo credentials are generated at seed time and written to a git-ignored local file, never committed.
