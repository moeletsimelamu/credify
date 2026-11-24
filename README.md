# Credify

**A simulated digital lending and credit assessment platform** — applicant onboarding, loan applications, transparent affordability calculations, an experimental and explainable credit-risk model, human-in-the-loop lending decisions, repayment tracking and portfolio analytics.

> ⚠️ **Educational project. Synthetic data only.** Credify does not connect to any real financial institution, credit bureau or payment provider, and its outputs are not legally compliant lending decisions. The machine-learning model is experimental and advisory; a human always records the final decision.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Requirements, domain model, architecture | ✅ Approved |
| 2 | Backend foundation, database, authentication | ✅ Implemented |
| 3 | Applicants, loan products, loan applications and review workflow | ✅ Implemented |
| 4 | Affordability and repayment engines, versioned affordability assessments, schedule previews | ✅ Implemented |
| 5 | Recommendations, maker–checker decisions, loan accounts, repayments, arrears, early settlement, portfolio analytics, demo seed | ✅ Implemented |
| 6 | Synthetic dataset, shared feature builder, baseline / logistic regression / gradient boosting training | ✅ Implemented |
| 7–10 | See [docs/ROADMAP.md](docs/ROADMAP.md) | Planned |

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — modules, roles and permissions, domain model, state machine, technology decisions
- [DATABASE.md](DATABASE.md) — schema, constraints, append-only tables, concurrency
- [FINANCIAL_CALCULATIONS.md](FINANCIAL_CALCULATIONS.md) — formulas, rounding rules, worked examples
- [docs/ML_STRATEGY.md](docs/ML_STRATEGY.md) — synthetic data, feature policy, evaluation, fairness
- [docs/ROADMAP.md](docs/ROADMAP.md) — phases and Definition of Done

## Quick start (Docker)

Prerequisites: Docker with Compose v2.

```bash
cp .env.example .env
```

Replace every `<placeholder>` in `.env` (generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`), then:

```bash
docker compose up --build -d
```

```bash
docker compose exec api python -m app.cli create-admin --email admin@example.com --full-name "Platform Admin"
```

The API is at <http://127.0.0.1:8000> — interactive docs at <http://127.0.0.1:8000/docs>, readiness at `/health/ready`.

### Demo data

Instead of creating an admin by hand, you can load the deterministic synthetic demo book (staff for every role, three products, 40 applicants with applications in every status, and 15 loan accounts with on-time, late, in-arrears, prepaid and settled histories):

```bash
docker compose run --rm seed
```

Add `--reset` to replace data that is already there, `--seed N` for a different but still reproducible book, or `--as-of YYYY-MM-DD` to choose the demo's "today". Passwords are generated on each run and written to `.local/demo-credentials.txt`, which is git-ignored. Every address uses the reserved `credify-demo.example` domain.

### Arrears job

Late fees, instalment statuses and each loan's arrears position are brought up to date by a daily job. Run it on demand, optionally for a later date to simulate the passage of time:

```bash
docker compose exec api python -m scripts.run_arrears_job --as-of 2027-01-31
```

## Backend development

Prerequisites: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you) and the database container.

```bash
docker compose up -d db
```

```bash
cd backend && uv sync
```

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn --factory app.main:create_app --reload
```

### Checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app tests scripts
```

```bash
uv run pytest
```

Integration tests run against the `credify_test` database (created automatically by the Postgres container) using the real Alembic migrations. They are skipped if `CREDIFY_TEST_DATABASE_URL` is not set.

## ML pipeline (offline)

The credit-risk model is trained offline in [`ml/`](ml/README.md), never inside a web request. From `ml/`:

```bash
uv sync
```

```bash
uv run python -m credify_ml.generate
```

```bash
uv run python -m credify_ml.train --check
```

The first command installs the pipeline (it reuses the backend's feature builder). The second writes the synthetic dataset to `ml/data/` (git-ignored). The third re-trains every model and confirms the metrics in [`ml/reports/phase6/training_summary.md`](ml/reports/phase6/training_summary.md) are reproduced; drop `--check` to overwrite them.

## Project ownership

Credify is a solo portfolio project. All commits are authored by the project owner.
