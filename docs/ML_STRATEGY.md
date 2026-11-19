# Credify — Synthetic Dataset and Credit Risk Modelling Strategy

> **Status:** Approved 2026-11-18. Becomes the basis for `MODEL_CARD.md` and `DATA_DICTIONARY.md` in Phases 6–7.
>
> The model is **experimental and advisory**. It is trained on synthetic data, so its metrics describe how well it recovers a simulated process — **not** real-world predictive accuracy. It never approves or declines anyone.

## 1. Why a custom synthetic generator

Public credit datasets (e.g. German Credit, Lending Club, Home Credit) either include protected attributes as features, have licensing constraints, or don't match Credify's schema. A **documented data-generating process (DGP)** gives us:

- Features that exactly match what the application collects, so training and serving use the same feature builder.
- A known ground truth, so we can check whether the model recovers real signal or spurious patterns.
- Controlled, documented bias injection so the fairness evaluation has something real to detect.
- Full reproducibility from a seed and a generator version.

## 2. Generator design (`ml/credify_ml/generate.py`)

- **Size:** 50,000 synthetic borrowers (configurable); seed `20261118`; generator version `synthgen-1.0.0`. Output: Parquet + a JSON manifest (row count, seed, version, SHA-256, column stats).
- **Stage 1 — latent context (audit-only):** synthetic `age_band` and `sex` attributes are generated *first* and used **only** to create realistic correlations (e.g. a modest income gap) and later for fairness auditing. They are written to a separate `audit_attributes` file and are **never** model inputs.
- **Stage 2 — observable features**, drawn from realistic distributions conditional on stage 1 where documented:
  - employment type (categorical) and tenure months (gamma);
  - gross monthly income (log-normal by employment type); net income as gross × an effective-deduction rate (beta);
  - living expenses (scaled to income with noise); existing debt instalments, number of credit accounts, revolving utilisation (beta);
  - missed payments in the last 12 months (zero-inflated Poisson), months since last delinquency;
  - requested amount, term, purpose; instalment from the real amortisation engine.
- **Stage 3 — derived features** using the *same* production code: DTI, repayment burden, residual income, loan-to-income.
- **Stage 4 — outcome:** `defaulted_12m` (simulated 90+ days past due within 12 months), sampled from `Bernoulli(sigmoid(β₀ + Σβᵢ·zᵢ + interactions + ε))`. Coefficients are documented with their intended direction (e.g. higher DTI → higher risk). `β₀` is calibrated to a target default rate of ~12 %. Label noise is added so the problem is not trivially separable.

The DGP coefficients are recorded in the manifest, so the evaluation report can compare learned effects to true effects.

## 3. Feature policy

**Model features (candidates):**

| Feature | Type | Source |
|---|---|---|
| `gross_monthly_income`, `net_monthly_income` | numeric | Income records |
| `employment_type` | categorical | Employment |
| `employment_tenure_months` | numeric | Employment |
| `living_expenses_applied` | numeric | Affordability |
| `existing_debt_instalments` | numeric | Commitments |
| `num_credit_accounts` | integer | Commitments |
| `revolving_utilisation` | ratio | Commitments (balance ÷ limit, revolving only) |
| `missed_payments_12m`, `months_since_delinquency` | integer | Simulated credit history (synthetic only) |
| `requested_amount`, `term_months`, `loan_purpose` | mixed | Application |
| `debt_to_income`, `repayment_burden`, `residual_income`, `loan_to_income` | numeric | Affordability engine |

**Excluded — never used as features:** age / date of birth, sex / gender, race / ethnicity, marital status, number of dependants, religion, nationality, disability, health, language, **postal code / city / region** (geographic proxies), names, email, phone. A unit test asserts the model's feature schema contains none of these and the feature builder cannot read them.

Feature definitions live in **one module** (`backend/app/modules/credit_risk/features.py`) imported by both the ML pipeline and the API, so training/serving skew is structurally prevented. Input validation rejects values outside documented ranges.

## 4. Modelling plan

- **Split:** stratified 70 / 15 / 15 train / validation / test, fixed seed. The test set is used once, for the final report.
- **Preprocessing:** scikit-learn `Pipeline` + `ColumnTransformer` (median imputation, standard scaling, one-hot for categoricals; `log1p` for skewed money features).
- **Models:**
  1. **Baseline:** majority-class / prior-rate model (sanity floor).
  2. **Logistic regression** (L2, class-weight tuned) — interpretable primary candidate.
  3. **Histogram gradient boosting** — non-linear challenger.
  4. Random forest only if the comparison justifies it.
- **Tuning:** stratified 5-fold CV on train; small, documented grids (no leakage from validation/test).
- **Calibration:** reliability curve + Brier score; isotonic or sigmoid calibration on validation if needed, since a PD must be interpretable as a probability.

**Serving choice (approved decision D4):** serve logistic regression unless gradient boosting is *materially* better (e.g. ROC-AUC +0.02 and better calibration), because LR gives exact, faithful per-prediction explanations. The decision and numbers will be recorded in the model card.

## 5. Evaluation

Reported on the held-out test set, in `ml/reports/<model_version>/`:

- Precision, recall, F1 (at the chosen threshold), ROC-AUC, PR-AUC, Brier score, log loss.
- Confusion matrix at the chosen threshold.
- **Threshold analysis:** table and curve of precision / recall / F1 / flagged-rate across thresholds 0.05–0.60. The operating threshold is chosen to favour recall on defaulters within an acceptable review workload, and justified in writing.
- Calibration curve; score distribution by outcome.
- Learned vs. true DGP effects (directional agreement).

## 6. Explainability

- **Logistic regression (exact):** per-prediction contribution of feature *j* = `coef_j × (x_j − x̄_j)` in log-odds space (after transformation), relative to the training-mean baseline. Stored as a ranked list with plain-language labels, e.g. "Debt-to-income 0.41 — increases risk (+0.38 log-odds)".
- **Gradient boosting (if served):** SHAP TreeExplainer values; global permutation importance in all cases.
- The UI shows the top factors increasing and decreasing risk, the base rate, and the caveat that contributions describe the *model*, not causation.

## 7. Risk bands

PD → band mapping (provisional; finalised from the test-set distribution):

| Band | PD range | Guidance shown to reviewers |
|---|---|---|
| A | < 3 % | Low modelled risk |
| B | 3 – 7 % | Below-average modelled risk |
| C | 7 – 15 % | Average modelled risk |
| D | 15 – 30 % | Elevated modelled risk — review carefully |
| E | ≥ 30 % | High modelled risk — review carefully |

Bands are *guidance*. The decision screen always requires the human to record their own reasons.

## 8. Fairness and bias evaluation

Using the audit-only synthetic attributes (never features):

- Rate of "elevated risk" (band D/E) per group; **disparate impact ratio** (four-fifths rule used as a heuristic, not a legal test).
- **Equal opportunity difference** (TPR gap) and FPR gap between groups.
- Calibration within groups.
- Proxy check: correlation / mutual information between each feature and each protected attribute; features with strong proxy behaviour are flagged in the report.
- Because the DGP deliberately injects an income gap, we *expect* some disparity via income-related features; the report explains this and shows the effect of mitigation options (e.g. threshold review) without claiming the problem is "solved".

## 9. Versioning, registry and serving

- `ml/credify_ml/register.py` saves the fitted pipeline with joblib, computes its SHA-256, and inserts a `model_versions` row (`CANDIDATE`) with feature schema, metrics, threshold, band cut-offs, dataset hash, seed, generator version and git commit.
- Activation is an explicit admin action (`CANDIDATE → ACTIVE`, previous `ACTIVE → RETIRED`), audited.
- **Security:** joblib/pickle can execute code on load, so the API loads artefacts **only** from the read-only model volume and **only** after verifying the SHA-256 recorded in the database. Uploading models through the API is not supported.
- Every scoring call stores `model_version_id`, the exact `feature_snapshot`, the PD, band and explanation in an immutable `credit_risk_assessments` row. Scoring failures are recorded too (status `FAILED`) and never block human review.

## 10. Limitations (to carry into the model card)

- Synthetic data reflects the assumptions of its generator, including the biases deliberately encoded in it.
- Metrics measure recovery of the simulated process, not real-world performance.
- No temporal validation, drift monitoring or reject inference (real lenders only observe outcomes for approved loans).
- Simulated credit-history features stand in for bureau data that real lenders would obtain with consent.
