# Credify — Architecture

> **Status:** Approved by the project owner on 2026-11-18 (Phase 1). Sections are marked as they are implemented.
>
> Credify is an **educational simulation** of a digital lending platform. It uses synthetic data only, connects to no real financial institution, bureau or payment provider, and does not produce legally compliant lending decisions.

---

## 1. Goals and non-goals

**Goals**

- Show a realistic loan lifecycle end to end: applicant → application → financial profile → affordability → experimental risk score → human recommendation → human decision → loan account → repayments → portfolio analytics.
- Financial arithmetic that is exact, reproducible and tested against independently computed results.
- A machine-learning component that is honest about what it is: experimental, advisory, explainable, versioned and audited.
- Professional engineering practice: migrations, RBAC enforced on the server, audit trails, structured logs, CI, tests at every layer.

**Non-goals**

- Real credit bureau, KYC, open-banking or payment integrations.
- Regulatory compliance (e.g. National Credit Act affordability regulations, GDPR/POPIA certification). Where we borrow ideas from regulation we say so and label them *simplified*.
- Microservices, event buses, Kubernetes. Credify is a **modular monolith**.
- Multi-tenancy and multi-currency conversion (each product has one currency; no FX).

---

## 2. System context

```
┌──────────────┐   HTTPS/JSON   ┌───────────────────────────────┐   SQL   ┌──────────────┐
│  React SPA   │ ─────────────▶ │  FastAPI modular monolith     │ ──────▶ │ PostgreSQL 16│
│ (Vite, TS)   │ ◀───────────── │  /api/v1                      │ ◀────── │              │
└──────────────┘  access JWT +  │                               │         └──────────────┘
                  refresh cookie│  ┌─────────────────────────┐  │
                                │  │ credit_risk module      │──┼──▶ model artefacts (read-only
                                │  │ loads registered model  │  │     volume, SHA-256 verified)
                                │  └─────────────────────────┘  │
                                └───────────────────────────────┘
                                              ▲
                     ml/ pipeline (offline) ──┘ registers model versions
                     (generate data → train → evaluate → register)
```

- **One deployable backend**, one database, one SPA. Docker Compose runs `db`, `api`, `web` (and a one-shot `migrate` + `seed`).
- **The ML pipeline is offline.** Training never happens inside a web request. The pipeline writes a versioned artefact and a metadata record; the API only *loads and scores*.

---

## 3. Module map (backend)

Each module owns its tables, its Pydantic schemas, its service functions and its router. Modules talk to each other through **service functions**, never by reaching into each other's tables.

| Module | Responsibility | Depends on |
|---|---|---|
| `identity` | Users, password hashing, login/refresh/logout, roles → permissions, current-user dependency | `core` |
| `audit` | Append-only audit event writer + query API | `core` |
| `products` | Loan product configuration (rate, limits, fees, affordability thresholds) | `identity`, `audit` |
| `applicants` | Applicant profile, contact, employment, income, expenses, existing credit commitments | `identity`, `audit` |
| `applications` | Loan applications, status state machine, review notes, information requests, document references | `applicants`, `products`, `audit` |
| `affordability` | **Pure** affordability calculator + persisted, versioned assessments | `applicants`, `applications`, `loans.engine` |
| `credit_risk` | Feature builder, model registry, scoring, explanations, assessment history | `applicants`, `applications`, `affordability` |
| `decisions` | Recommendations and final human lending decisions (maker–checker) | `applications`, `affordability`, `credit_risk` |
| `loans` | **Pure** amortisation engine + loan accounts, schedules, repayments, allocation, arrears, settlement quotes | `decisions`, `products` |
| `analytics` | Read-only portfolio aggregates and model-metric reporting | all (read-only queries) |

### 3.1 Layering inside a module

```
modules/<name>/
  domain/        # pure Python: dataclasses, enums, calculators, state machine. No I/O, no SQLAlchemy.
  models.py      # SQLAlchemy ORM tables
  schemas.py     # Pydantic request/response models (API contract)
  service.py     # use cases: load → call domain → persist → audit, inside one transaction
  router.py      # FastAPI endpoints: auth/permission deps, call service, map errors
```

- **Domain code is pure** so the affordability and repayment engines can be unit-tested exhaustively with no database.
- **No repository layer by default.** SQLAlchemy 2.0's `Session` + `select()` is already a repository abstraction; a thin extra layer would be ceremony. Query helpers live in `service.py` or a `queries.py` when they grow.
- **Transactions** are opened per request by a dependency (`get_session`) and committed once at the end of a successful use case. Audit events are written in the *same* transaction as the change they describe, so an audit record can never exist without its change (or vice versa).

### 3.2 Cross-cutting `core` package

- `config.py` — `pydantic-settings` `Settings`; the app **refuses to start** if required variables are missing or malformed (e.g. `JWT_SECRET` shorter than 32 bytes, invalid `DATABASE_URL`).
- `db.py` — engine, session factory, `Base`, naming convention for constraints (so Alembic diffs are stable).
- `security.py` — Argon2id hashing, JWT encode/decode, refresh-token hashing.
- `money.py` — `Money` value object (`Decimal` + ISO-4217 code), quantisation helpers, `ROUND_HALF_UP` context.
- `errors.py` — domain exceptions → RFC 9457 `application/problem+json` responses with a stable `code`.
- `logging.py` — `structlog` JSON logs with `request_id`, `user_id`, `route`, latency; **never logs** passwords, tokens or full financial payloads.
- `clock.py` — injectable UTC clock so time-dependent logic (arrears, due dates) is testable.

---

## 4. Roles and permissions

Permissions are checked **on the server** via a dependency, e.g. `Depends(require("application:decide"))`. The frontend hides controls for usability only.

| Permission | Admin | Loan Officer | Credit Analyst | Applicant |
|---|:-:|:-:|:-:|:-:|
| `user:manage` | ✅ | | | |
| `product:manage` | ✅ | | | |
| `audit:read` | ✅ | | | |
| `applicant:read_any` | ✅ | ✅ | ✅ | |
| `applicant:write_own` | | | | ✅ |
| `application:create_own` / `submit_own` / `withdraw_own` | | | | ✅ |
| `application:read_any` | ✅ | ✅ | ✅ | |
| `application:assign` / `start_review` | ✅ | ✅ | | |
| `application:request_info` | | ✅ | ✅ | |
| `application:note` (internal review notes) | ✅ | ✅ | ✅ | |
| `affordability:run` | | ✅ | ✅ | |
| `risk:run` / `risk:annotate` | | | ✅ | |
| `application:recommend` | | ✅ | | |
| `application:decide` | ✅ | | ✅ | |
| `loan:disburse` | ✅ | ✅ | | |
| `repayment:record` (simulated, any loan) | ✅ | ✅ | | |
| `repayment:record_own` (simulated, own loan) | | | | ✅ |
| `analytics:portfolio` | ✅ | ✅ | ✅ | |
| `analytics:model` | ✅ | | ✅ | |

**Object-level rules** (enforced in services, not just routes):

- Applicants see only their own applicant record, applications and loan accounts. Requests for another applicant's resources return `404` (not `403`) to avoid confirming existence.
- **Maker–checker:** the user who records the final decision must be different from the user who recorded the recommendation. *(Approved decision D2.)*
- Applicants self-register. Staff accounts are created only by an administrator. There is no self-service role elevation.

---

## 5. Domain model

### 5.1 Core entities

```
User 1───0..1 Applicant 1───* EmploymentRecord
                        1───* IncomeRecord
                        1───* ExpenseRecord
                        1───* CreditCommitment
                        1───* LoanApplication *───1 LoanProduct
                                   │
          ┌────────────┬───────────┼──────────────┬──────────────┬────────────────┐
          *            *           *              *              *                0..1
 StatusHistoryEntry  ReviewNote  InformationRequest  AffordabilityAssessment  CreditRiskAssessment  LendingDecision
                                  DocumentReference   (versioned, immutable)   (versioned, immutable)     │
                                                                              *──1 ModelVersion            │
          Recommendation (*) ──────────────────────────────────────────────────────────────────────────────┘
                                                                                                    0..1
                                                                                              LoanAccount
                                                                                       1───* RepaymentSchedule (versioned)
                                                                                                1───* ScheduleInstalment
                                                                                       1───* LoanTransaction (statement, append-only)
                                                                                                1───* PaymentAllocation
AuditEvent (append-only, references any entity by type + id)
```

### 5.2 Key invariants

1. **Money is never a float.** Amounts are `Decimal` in Python and `NUMERIC` in PostgreSQL, always paired with a currency code.
2. **Assessments are immutable.** Re-running affordability or risk creates a new version; the old row is never updated. Each stores the exact input snapshot and calculator/model version that produced it.
3. **Decisions are immutable.** A final decision cannot be edited. Corrections would require a new, separately audited action (out of scope).
4. **The model never decides.** No code path transitions an application to `APPROVED`/`DECLINED` without an authenticated human with `application:decide`, a recorded explanation and reason codes.
5. **Status changes only through the state machine** (below). Each transition writes a `StatusHistoryEntry` and an `AuditEvent` atomically.
6. **Loan balances are derived from the transaction log.** Cached balances on `LoanAccount` are updated in the same transaction and can be recomputed and reconciled from the log.

### 5.3 Application state machine

Spec statuses plus two approved additions: **`DRAFT`** (applicant is still entering financial information; nothing is assessed yet) and **`WITHDRAWN`** (applicant cancels before a decision). *(Approved decision D3.)*

```
            submit (applicant; profile complete)
  DRAFT ───────────────────────────────▶ SUBMITTED
    │                                       │  system: run affordability + risk scoring
    │ withdraw                              ▼
    ▼                                 PENDING_REVIEW ◀──────────────────────────┐
 WITHDRAWN ◀── withdraw (any state          │ start review (officer; self-assign) │
               before a final decision)     ▼                                     │
                                       UNDER_REVIEW ──request info──▶ ADDITIONAL_INFO_REQUESTED
                                        │   ▲   │                          │ applicant responds
                         recommend      │   │   │ recommend reject         └──(re-runs assessments)──┘
                         approve        ▼   │   ▼
                         RECOMMENDED_APPROVAL  RECOMMENDED_REJECTION
                                 │   └─ send back ─┘   │
                     decide (analyst/admin ≠ recommender; reasons + explanation)
                                 ▼                     ▼
                              APPROVED              DECLINED
                                 │ disburse (simulated)
                                 ▼
                         LoanAccount: ACTIVE ⇄ IN_ARREARS → SETTLED
```

Guard conditions (enforced in `applications/domain/state_machine.py`):

| Transition | Who | Guard |
|---|---|---|
| `DRAFT → SUBMITTED` | Applicant (owner) | Amount/term within product limits; ≥1 income record; expenses captured; employment captured |
| `SUBMITTED → PENDING_REVIEW` | System | Affordability assessment v1 persisted; risk assessment attempted (failure is recorded, not fatal) |
| `PENDING_REVIEW → UNDER_REVIEW` | Loan officer | Application assigned to that officer |
| `UNDER_REVIEW → ADDITIONAL_INFO_REQUESTED` | Officer / analyst | Open information request created with a message |
| `ADDITIONAL_INFO_REQUESTED → PENDING_REVIEW` | Applicant | All open requests answered; profile still complete and single-currency (it was editable); new assessment versions generated |
| `UNDER_REVIEW → RECOMMENDED_*` | Loan officer | Latest affordability assessment exists; rationale ≥ 20 chars; links the assessment versions reviewed |
| `RECOMMENDED_* → UNDER_REVIEW` | Analyst / admin | Reason required |
| `RECOMMENDED_* → APPROVED / DECLINED` | Analyst / admin | Decider ≠ recommender; ≥1 reason code; explanation; acknowledges the model output is advisory; records whether the decision departs from the model's band (override flag) |
| `APPROVED → (LoanAccount created)` | Officer / admin | Idempotent; creates account + schedule v1 + disbursement transaction |
| `* → WITHDRAWN` | Applicant | Not yet `APPROVED`/`DECLINED` |

Concurrent transitions are prevented with **optimistic locking** (`version_id` column); a stale write returns `409 Conflict`.

### 5.4 Loan account lifecycle

`PENDING_DISBURSEMENT → ACTIVE ⇄ IN_ARREARS → SETTLED` (plus `CLOSED` for administrative closure). Arrears status is recalculated whenever a repayment is recorded or the daily arrears job runs (a CLI command / scheduled task, also callable from tests with a fixed clock).

---

## 6. API design

- Base path `/api/v1`, JSON, OpenAPI generated by FastAPI and used to **generate TypeScript types** for the frontend (`openapi-typescript`) so the contract cannot silently drift.
- Resource-oriented, with explicit **action endpoints** for state transitions (they are commands, not field edits):
  - `POST /applications/{id}/submit`, `/start-review`, `/request-information`, `/recommendation`, `/decision`, `/withdraw`
  - `POST /applications/{id}/affordability-assessments` (creates a new version) · `GET …/affordability-assessments` · `GET …/affordability-assessments/{id}` · `POST /applications/{id}/resume-review`
  - `GET /products/{id}/repayment-schedule?amount=&term_months=` and `GET /applications/{id}/repayment-schedule` (illustrative previews; nothing stored)
  - `POST /applications/{id}/risk-assessments` · `GET …/risk-assessments`
  - `POST /loan-accounts/{id}/repayments` (requires `Idempotency-Key` header) · `GET /loan-accounts/{id}/schedule` · `GET /loan-accounts/{id}/settlement-quote?as_of=YYYY-MM-DD`
- Pagination: `?page=&page_size=` (max 100) with `total`; filtering on status, date range, product.
- Errors: RFC 9457 problem details, e.g. `{"type": ".../invalid-transition", "title": "Invalid status transition", "status": 409, "code": "INVALID_TRANSITION", "detail": "..."}`.
- Money in JSON is a **string** (`"12500.00"`) plus `"currency": "ZAR"` — never a JSON number, to avoid float parsing in clients.

---

## 7. Authentication

- **Password hashing:** Argon2id (`argon2-cffi`) with library-recommended parameters; rehash on login if parameters change.
- **Tokens:** short-lived access JWT (15 min, HS256, `sub`, `role`, `exp`, `jti`) held in memory by the SPA; refresh token (opaque, 256-bit random, 7 days) in an `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api/v1/auth`, **stored hashed** and **rotated** on every use. Reuse of a revoked refresh token revokes the whole token family.
- **Login protection:** generic error messages, per-IP + per-account attempt throttling, audit of failures.
- **CORS** restricted to the configured frontend origin.

---

## 8. Frontend architecture

- **Stack:** React + TypeScript (strict) + Vite, React Router (data routers), TanStack Query for server state, React Hook Form + Zod for forms, Radix UI primitives for accessible dialogs/menus/tabs, Recharts for charts, Tailwind CSS driven by a custom design-token theme.
- **Structure:** `src/features/<module>/` (pages, components, queries, schemas) mirroring backend modules; `src/shared/` for design system components (`MoneyText`, `StatusBadge`, `DataTable`, `EmptyState`, `ErrorState`, `Skeleton`).
- **Role-aware shells:** an *Applicant portal* (my applications, financial profile, my loan, repayments) and a *Back office* (work queue, application workspace, portfolio, model monitoring, admin).
- **Money formatting** uses `Intl.NumberFormat` with the currency from the API; amounts are never re-calculated on the client — the server is the source of truth.

### 8.1 Design direction (from the reference set)

What we take from the references, and what we avoid:

- **Take:** the dark ink sidebar + light canvas (Lonico, credit-risk dashboard); the *loan workspace with a right-hand snapshot rail and audit timeline* (LEXA Ledger); dense, legible amortisation tables (HES back office, FinanceLend); a horizontal workflow stepper (credit-risk dashboard); KPI rows with small trend lines.
- **Avoid:** decorative gauges with no decision value, purple-gradient "AI dashboard" styling, fake precision, and charts that don't answer a question.
- **Credify specifics:**
  - **Application workspace** with tabs *Overview · Financial profile · Affordability · Credit risk · Decision · Loan & repayments · Audit*, and a persistent snapshot rail (requested amount, term, instalment, residual income, risk band, current status, assigned officer).
  - **Affordability waterfall** (net income → living expenses → existing debt → proposed instalment → residual) instead of a gauge — it shows *why*, not just a number.
  - **Risk workspace** that visually separates *"Experimental model output — advisory only"* from the human decision panel, with a signed factor-contribution chart.
  - Typography: IBM Plex Sans for UI, IBM Plex Mono / tabular numerals for all figures so columns of money align. One restrained accent colour; semantic colours (positive / warning / critical) reserved for status.
  - Every list has loading skeletons, an empty state that says what to do next, and an error state with a retry.

---

## 9. Observability, data integrity and audit

- Structured JSON logs with a `request_id` propagated via `X-Request-ID`.
- `audit_events` table, append-only (an `UPDATE`/`DELETE` trigger raises an exception), recording actor, role, action, entity, before/after diff (financial values included, secrets never), request id and UTC timestamp.
- All timestamps `TIMESTAMPTZ` stored in UTC; due dates are calendar `DATE`s.
- Idempotency keys on repayment recording and loan disbursement.
- Health endpoints: `/health/live`, `/health/ready` (DB reachable, active model artefact hash verified).

---

## 10. Repository layout (proposed)

```
credify/
├── backend/
│   ├── app/{core,modules/*,main.py}
│   ├── migrations/            # Alembic
│   ├── scripts/               # seed_demo_data.py, run_arrears_job.py
│   ├── tests/{unit,integration}
│   └── pyproject.toml
├── ml/
│   ├── credify_ml/            # generate.py, train.py, evaluate.py, fairness.py, register.py
│   ├── notebooks/             # exploration only; pipeline is the source of truth
│   ├── reports/               # generated evaluation reports (committed)
│   └── artefacts/             # model binaries (git-ignored; reproducible from seed)
├── frontend/
│   ├── src/{app,features,shared,api}
│   ├── tests/                 # Vitest unit tests
│   └── e2e/                   # Playwright
├── docs/                      # ROADMAP.md, ML_STRATEGY.md, ADRs
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
└── README.md, ARCHITECTURE.md, DATABASE.md, FINANCIAL_CALCULATIONS.md, MODEL_CARD.md,
    DATA_DICTIONARY.md, SECURITY.md, TESTING.md, DEPLOYMENT.md
```

---

## 11. Technology decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Python **3.12** (in Docker) | Broadest wheel support for scikit-learn/pandas/psycopg; local machine has 3.14, so the container (and `uv`-managed interpreter) pins 3.12 for reproducibility |
| Python packaging | **uv** + `pyproject.toml` + lockfile | Fast, reproducible installs; can provision Python 3.12 locally |
| Web framework | FastAPI + Pydantic v2 | Typed request/response validation, OpenAPI generation |
| ORM / migrations | SQLAlchemy 2.0 (sync) + Alembic | Sync sessions keep transactions and testing simple; the workload is not I/O-bound enough to justify async complexity |
| DB driver | psycopg 3 | Maintained, supports PostgreSQL features we use |
| Database | PostgreSQL 16 | `NUMERIC`, `CHECK` constraints, partial unique indexes, `JSONB` snapshots, triggers for append-only tables |
| Password hashing | Argon2id | Current OWASP recommendation |
| Logging | structlog (JSON) | Structured, contextual logs |
| ML | scikit-learn, pandas, NumPy; joblib artefacts | Standard, explainable baselines; no deep learning needed |
| Frontend | React + TS + Vite + React Router + TanStack Query | Per spec |
| Forms / validation | React Hook Form + Zod | Accessible, typed forms; mirrors server validation |
| Testing | pytest, httpx TestClient, Testcontainers-style Postgres in CI (service container), Vitest + Testing Library, Playwright | Each layer tested where it is cheapest |
| Lint / format / types | ruff, mypy (strict on `domain/`), ESLint, Prettier, `tsc --noEmit` | Enforced in CI |
| Secrets scanning | gitleaks (pre-push + CI) | Requirement: check for secrets before every push |

Exact library versions are pinned in Phase 2 to the latest stable releases at that time and recorded in lockfiles.

---

## 12. Approved Phase 1 decisions (2026-11-18)

| # | Decision | Outcome |
|---|---|---|
| D1 | Default currency | ZAR; every amount carries an explicit ISO-4217 code |
| D2 | Final decision authority | Credit Analyst or Administrator, and never the same user who recorded the recommendation (maker–checker) |
| D3 | Extra statuses | `DRAFT` and `WITHDRAWN` added to the specified eight |
| D4 | Served model | Logistic regression unless gradient boosting is materially better (exact explanations) |
| D5 | Python tooling | uv with Python 3.12 pinned |

## 13. Phase 3 implementation decisions (2026-11-23)

Choices made while building applicants and applications, within the approved design:

| Topic | Decision |
|---|---|
| Business date | Calendar dates (age check, employment dates, reference year) use South African Standard Time, a fixed UTC+2 offset with no daylight saving. Timestamps stay in UTC. |
| One open application | An applicant may hold at most one application that is not `APPROVED`, `DECLINED` or `WITHDRAWN`, enforced by a partial unique index. Assessments therefore always describe a single request. |
| Profile lock | Profile and financial records are read-only while an application is `SUBMITTED`, `PENDING_REVIEW`, `UNDER_REVIEW` or `RECOMMENDED_*`. They are editable in `DRAFT` and `ADDITIONAL_INFO_REQUESTED`, and once the application is decided or withdrawn. Violations return `409 PROFILE_LOCKED`. |
| Who reviews | Admins and loan officers can assign an application, but only a loan officer can start a review (per the §5.3 guard table). An unassigned application is claimed by the officer who starts reviewing it. |
| Internal notes | New permission `application:note` (Admin, Loan Officer, Credit Analyst). Applicants cannot read or write review notes. |
| Concurrency | Transitions lock the application row (`SELECT … FOR UPDATE`). Clients may send `If-Match: "<version>"`; a mismatch returns `409 VERSION_CONFLICT`. `version_id` also catches any stale ORM write (`409 CONCURRENT_MODIFICATION`). |
| Currency consistency | Submission requires every active income, expense and commitment record to be in the application's currency, because affordability cannot mix currencies. |
| Documents | Document references are metadata only (type, file name, content type, size). The storage key is generated by the server under `simulated/`, and no file content is accepted. |
| Phase boundary | `SUBMITTED → PENDING_REVIEW` and `ADDITIONAL_INFO_REQUESTED → PENDING_REVIEW` require persisted assessments, so they are implemented in the state machine but wired to the API in Phase 4 (see §14). Recommendations and decisions are wired in Phase 5. |

## 14. Phase 4 implementation decisions (2026-11-23)

| Topic | Decision |
|---|---|
| Submission | Submitting runs affordability assessment v1 and moves the application to `PENDING_REVIEW` in the same transaction, so the review queue never holds an unassessed application. The queueing step is recorded as the system's (no actor) with the outcome as its reason. Risk scoring joins this step in Phase 7. |
| Unaffordable outcomes | Every outcome, including `UNAFFORDABLE` and `INSUFFICIENT_DATA`, is queued for a human. The assessment is evidence for the reviewer, never a decision. |
| Resuming review | `POST /applications/{id}/resume-review` (applicant). Because the profile was editable while information was outstanding, the guard re-checks profile completeness and currency, then a new assessment version (`RESUBMISSION`) is recorded before the status changes. Missing answers are reported before anything is assessed. |
| Staff re-runs | Loan officers and credit analysts (`affordability:run`) can create a new version while the application is `PENDING_REVIEW`, `UNDER_REVIEW` or `ADDITIONAL_INFO_REQUESTED`. Re-runs are blocked once a recommendation exists, so the evidence it cites stays the latest. |
| Terms used | Assessments always use the product terms frozen at submission (`product_terms_snapshot`), never the product's current terms. Previews for a draft use the current terms. |
| Who can read | Applicants can read their own assessments (the calculation is meant to be transparent); staff can read all. Other applicants' assessments are `404`. |
| Ratio rounding | Ratios are rounded to four places before the threshold rules compare them, so the stored figures always explain the stored outcome exactly. |
| Expense norm | The illustrative norm is a configuration object (`illustrative-2026`) recorded in every assessment's input snapshot, so a later change never alters how an old assessment reads. |
| Rejected income | Income a reviewer marks `REJECTED` is kept in the snapshot with `included: false` and excluded from the totals. Staff verification has no endpoint yet. |
| Unamortisable terms | The engine rejects terms that cannot be repaid in whole-cent instalments (see FINANCIAL_CALCULATIONS.md §3.3) instead of producing a schedule with a flat or negative balance. |
| Floats | A unit test parses every pure financial package and fails on float literals, `float(...)` or the `math` module. |
| Ordering | `uuid7()` is now strictly increasing within a process (RFC 9562 §6.2 counter), so rows written in one transaction with the same timestamp, such as the two status changes of a submission, keep their order. |
| Test-only dependencies | `hypothesis` (property tests) and `numpy-financial` (independent cross-check of the instalment and interest figures). Neither is imported by application code. |

