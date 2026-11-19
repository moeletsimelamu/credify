# Credify — Implementation Roadmap

> **Status:** Approved 2026-11-18.

Each phase ends with: a summary of what was implemented, the list of changed files, test results, outstanding issues, and — once the repository exists — a reviewed commit set pushed to the owner's GitHub repository (owner as sole author, conventional commits, secrets scan before push).

| # | Phase | Deliverables | Exit criteria |
|---|---|---|---|
| 1 | Requirements, domain modelling, architecture | `ARCHITECTURE.md`, `DATABASE.md`, `FINANCIAL_CALCULATIONS.md`, `docs/ML_STRATEGY.md`, this roadmap | Owner approves design and resolves open decisions |
| 2 | Backend foundation, database, authentication | Repo scaffold, `uv` project, FastAPI app factory, settings validation, structlog, error model, SQLAlchemy base + Alembic, `users`/`refresh_tokens`/`audit_events`, Argon2id, JWT + rotating refresh, RBAC dependency, health endpoints, Docker Compose (db, api), `.env.example`, CI (ruff, mypy, pytest, gitleaks), `.gitignore` | `docker compose up` gives a healthy API; auth + RBAC integration tests pass against real Postgres in CI |
| 3 | Applicant and loan application management | `products`, `applicants` (employment, income, expenses, commitments), `applications` with state machine, status history, review notes, information requests, document references, ownership checks | State machine unit tests cover every valid and invalid transition; API tests prove applicants cannot read others' data |
| 4 | Affordability and repayment engines | Pure `affordability` and `loans.engine` packages; persisted affordability assessments; schedule generation endpoint (preview) | Golden vectors + property tests pass; independent `numpy-financial` cross-check passes |
| 5 | Lending decision workflow | Recommendations, maker–checker decisions, reason codes, loan account creation (simulated disbursement), repayments, allocation waterfall, arrears job, late fees, early settlement quotes, demo seed script | End-to-end API test: applicant → submission → review → decision → loan → payments → arrears → settlement |
| 6 | Synthetic dataset and ML experimentation | `ml/` package, generator + manifest, shared feature builder, baseline / LR / GBM training, exploration notebook | Reproducible dataset (hash stable for a seed); training script reproduces metrics |
| 7 | Evaluation, explainability, responsible AI | Evaluation + threshold + calibration reports, fairness report, explanations, model registry + activation, risk assessment endpoints, `MODEL_CARD.md`, `DATA_DICTIONARY.md` | Scoring stores version + snapshot + explanation; tests assert no protected features; hash-verified loading |
| 8 | Frontend and reporting | Design tokens + shared components, applicant portal, back-office work queue, application workspace (all tabs), affordability waterfall, risk workspace, schedules, loan history, portfolio + model dashboards, admin screens | Vitest component tests; accessible forms (labels, errors, keyboard); loading/empty/error states on every data view |
| 9 | Security, integration and E2E testing | Threat-model review, rate limiting, security headers, dependency audit, Playwright journeys for the 12 Definition-of-Done steps, CI E2E job, `SECURITY.md`, `TESTING.md` | All DoD steps automated in Playwright and green in CI |
| 10 | Final engineering review and documentation | Code review pass, `README.md`, `DEPLOYMENT.md` (local/Compose only — real deployment deferred to the portfolio phase), final doc consistency check | A new reviewer can clone, follow README, run the stack and tests |

## Definition of Done → where it is satisfied

| DoD step | Phase |
|---|---|
| 1. Create an applicant | 3 (API), 8 (UI) |
| 2. Submit a loan application | 3, 8 |
| 3. Enter financial information | 3, 8 |
| 4. Generate an affordability assessment | 4, 8 |
| 5. Generate a repayment schedule | 4, 8 |
| 6. View an experimental credit risk assessment | 7, 8 |
| 7. Inspect prediction factors | 7, 8 |
| 8. Record a human lending decision | 5, 8 |
| 9. Track simulated repayments | 5, 8 |
| 10. View portfolio analytics | 5 (API), 8 (UI) |
| 11. Run automated tests | 2 onward; complete in 9 |
| 12. Follow documented setup instructions | 10 |

## Explicitly out of scope until the portfolio-wide phase

Cloud deployment, custom domains, cross-project branding standardisation and portfolio polishing.
