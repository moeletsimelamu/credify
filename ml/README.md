# credify-ml — offline credit-risk pipeline

> **Educational and synthetic.** Every borrower is generated from a documented process. The model is experimental and advisory; it never approves or declines anyone. Its metrics describe how well it recovers the simulated process, not real-world performance.

Training never happens inside the API. This project generates the synthetic dataset, builds features with the **same code the API uses** (`backend/app/modules/credit_risk/features.py`) and trains the candidate models. The model card is [MODEL_CARD.md](../MODEL_CARD.md).

## Commands (from `ml/`)

```bash
uv sync
```

```bash
uv run python -m credify_ml.generate
```

```bash
uv run python -m credify_ml.train
```

```bash
uv run python -m credify_ml.train --check
```

- `generate` writes `data/borrowers.parquet`, `data/audit_attributes.parquet` and `data/manifest.json` (git-ignored; `--rows` and `--seed` override the defaults).
- `train` tunes on the training split, compares on the validation split, saves fitted pipelines to `artefacts/` (git-ignored) and writes [`reports/phase6/training_summary.md`](reports/phase6/training_summary.md).
- `train --check` re-trains and fails if any validation metric differs from the committed summary by more than 0.0001. CI runs it on every push.
- `evaluate` makes the Phase 7 choices on validation (calibration, threshold, bands), scores the test split once, and writes the packaged model to `artefacts/credify-pd-1.0.0/` and [`reports/credify-pd-1.0.0/evaluation.md`](reports/credify-pd-1.0.0/evaluation.md).
- `fairness` writes [`reports/credify-pd-1.0.0/fairness.md`](reports/credify-pd-1.0.0/fairness.md) from the audit-only attributes.
- `register` copies the artefact into `artefacts/registry/` (mounted read-only by the API) and records it as a `CANDIDATE` in the database named by `CREDIFY_DATABASE_URL`. An administrator activates it through the API.
- `evaluate --check` and `fairness --check` confirm the committed reports reproduce (CI runs both).

The exploration notebook needs the optional group: `uv sync --group notebook`, then open `notebooks/01_exploration.ipynb`.

## Layout

| Path | What it is |
|---|---|
| `credify_ml/generate.py` | Synthetic borrowers in four stages (latent context, profile, production affordability + shared features, outcome) |
| `credify_ml/dgp.py` | The documented outcome process: coefficients, intended directions, noise |
| `credify_ml/models.py` | Baseline, logistic regression and gradient boosting pipelines with their grids |
| `credify_ml/train.py` | Split, cross-validated tuning, validation metrics, summary and reproducibility check |
| `reports/phase6/` | Committed training summary |
| `notebooks/` | Exploration of the training split only |

The `sex` and `age_band` audit attributes are written to a separate file and are never model inputs. See [docs/ML_STRATEGY.md](../docs/ML_STRATEGY.md).
