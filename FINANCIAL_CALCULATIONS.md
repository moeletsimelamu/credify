# Credify — Financial Calculations

> **Status:** Approved 2026-11-18.
>
> **Important:** These calculations are a transparent, *simplified* educational model. They are **not** a legally compliant affordability assessment under any credit regulation (for example, South Africa's National Credit Act affordability regulations), and the expense norm table below is illustrative only.

## 1. Numeric rules (apply everywhere)

| Rule | Detail |
|---|---|
| Type | Python `decimal.Decimal` only. Floats are rejected at the API boundary (money arrives as strings) and are banned in `domain/` by a lint rule and a unit test |
| Context | Precision 28 significant digits; `ROUND_HALF_UP` |
| Money quantisation | `0.01` (cents). Quantised **at defined points only** (listed per formula), never repeatedly mid-calculation |
| Ratios | Computed at full precision; stored/displayed quantised to `0.0001` |
| Rates | Stored as decimal fractions (`0.215000` = 21.5 % p.a.) |
| Currency | Every amount carries an ISO-4217 code. Arithmetic across different currencies raises `CurrencyMismatchError`. No FX conversion |
| Day count | Actual/365 Fixed — used only for accrued interest in settlement quotes |
| Versioning | Each calculator exposes a `VERSION` string stored with every result (e.g. `affordability-1.0.0`, `amortisation-1.0.0`). A formula change bumps the version; old results keep the version that produced them |

## 2. Income normalisation

Each income record is converted to a monthly amount (gross and net separately), at full precision:

| Frequency | Monthly equivalent |
|---|---|
| `WEEKLY` | amount × 52 / 12 |
| `FORTNIGHTLY` | amount × 26 / 12 |
| `MONTHLY` | amount |
| `ANNUALLY` | amount / 12 |

- **Gross monthly income** `G = q(Σ monthly gross of active, non-rejected income records)`
- **Net monthly income** `N = q(Σ monthly net …)`

Net income is **declared per record** (after tax and statutory deductions) rather than computed from tax tables — tax rules are jurisdiction-specific and out of scope. Validation: `0 < net ≤ gross` per record.

`q(x)` = quantise to cents, `ROUND_HALF_UP`.

## 3. Loan instalment and amortisation (reducing balance)

Inputs: principal `P`, nominal annual rate `R`, term `n` months.

- **Principal financed:** `P = approved_amount + initiation_fee` (fee capitalised). With a zero fee, `P` is the approved amount.
- **Monthly rate:** `r = R / 12` (nominal rate compounded monthly; full precision, not rounded).
- **Instalment:**

  ```
  A = q( P · r / (1 − (1 + r)^(−n)) )     if r > 0
  A = q( P / n )                           if r = 0
  ```

- **Schedule**, for k = 1 … n, starting with `B₀ = P`:

  ```
  interest_k  = q(B_{k−1} · r)
  principal_k = A − interest_k               (k < n)
  principal_n = B_{n−1}                       (final instalment clears the balance exactly)
  instalment_k = interest_k + principal_k
  B_k = B_{k−1} − principal_k
  ```

  The final instalment absorbs accumulated rounding differences, so `B_n = 0.00` exactly and `Σ principal_k = P`.
- **Monthly service fee** `F` is added to each instalment as a separate component: `total_due_k = instalment_k + F`. It is not amortised and accrues no interest.
- **Due dates:** `due_k = disbursed_on + k months`, keeping the same day-of-month and clamping to the last day of shorter months (31 Jan → 28/29 Feb → 31 Mar, using the *original* day each time, not the clamped one).
- **Totals:** `total_interest = Σ interest_k`; `total_repayable = Σ total_due_k`; `cost_of_credit = total_repayable − approved_amount`.

### 3.1 Worked example A (canonical test vector)

`P = 100,000.00`, `R = 12 %`, `n = 12` → `r = 0.01`, `A = 8,884.88` (matches spreadsheet `PMT(1%,12,-100000)`).

| k | Instalment | Interest | Principal | Closing balance |
|---:|---:|---:|---:|---:|
| 1 | 8,884.88 | 1,000.00 | 7,884.88 | 92,115.12 |
| 2 | 8,884.88 | 921.15 | 7,963.73 | 84,151.39 |
| 3 | 8,884.88 | 841.51 | 8,043.37 | 76,108.02 |
| … | … | … | … | … |
| 11 | 8,884.88 | 175.07 | 8,709.81 | 8,796.88 |
| 12 | **8,884.85** | 87.97 | 8,796.88 | **0.00** |

Total interest 6,618.53; total repaid 106,618.53. Note the final instalment is 0.03 lower — the rounding adjustment.

### 3.2 Worked example B

`P = 30,000.00`, `R = 21.5 %`, `n = 24` → `A = 1,548.95`, final instalment `1,548.87`, total interest `7,174.72`.

### 3.3 Verification strategy

- Golden test vectors (A, B, zero-rate, 1-month term, 120-month term, high rate) with expected values computed **independently** — by spreadsheet `PMT/IPMT` and by `numpy-financial` (a test-only dependency, never used in production code).
- Property-based tests (Hypothesis) over random valid inputs: `B_n == 0`, `Σ principal == P`, every `interest_k ≥ 0`, every non-final instalment equals `A`, balances strictly decreasing, and `|final − A| ≤ 0.01 × Σ_{k=0…n} (1 + r)^k`.
  - *Correction (Phase 4):* the bound first written here, `|final − A| < n × 0.01`, holds only at low rates. Each period adds up to one cent of rounding error, and the error already in the balance accrues interest, so the drift compounds. Hypothesis found a counterexample (1,000.00 at 34.0157 % over 62 months: final differs by 0.63, above 0.62). The compounding bound above reduces to `0.01 × (n + 1)` at a zero rate.
- Terms that cannot be amortised in whole cents (an instalment that would leave the balance flat, growing or cleared before the last period, e.g. 1.00 over 60 months) are rejected with a clear error rather than producing a broken schedule.

## 4. Affordability assessment

All inputs are monthly and in the application currency.

| Symbol | Name | Formula / source | Quantised |
|---|---|---|---|
| `G` | Gross monthly income | §2 | yes |
| `N` | Net monthly income | §2 | yes |
| `E_d` | Declared living expenses | Σ active expense records | yes |
| `E_n` | Expense norm (minimum) | Table §4.1 applied to `G` | yes |
| `E` | Applied living expenses | `max(E_d, E_n)` | — |
| `C` | Existing debt instalments | Σ active credit commitment instalments | yes |
| `DI` | Disposable income | `N − E − C` | — |
| `I` | Proposed instalment | First-period `total_due` from §3 for the requested amount/term (incl. service fee) | — |
| `RI` | Residual income | `DI − I` | — |
| `DTI` | Debt-to-income ratio | `(C + I) / G` | 4 dp |
| `RB` | Repayment burden | `(C + I) / N` | 4 dp |
| `NB` | New-loan burden | `I / N` | 4 dp |
| `Buf` | Required buffer | `max(min_residual_amount, min_residual_rate × N)` | yes |

Thresholds (`max_dti`, `warn_dti`, `min_residual_amount`, `min_residual_rate`) come from the loan product and are frozen into the application's `product_terms_snapshot` at submission.

### 4.1 Illustrative expense norm (configurable, *not* a regulatory table)

| Gross monthly income band | Fixed minimum | + rate on income above band floor |
|---|---:|---:|
| 0.00 – 6,000.00 | 2,000.00 | 0 % |
| 6,000.01 – 15,000.00 | 2,000.00 | 10 % |
| 15,000.01 – 30,000.00 | 2,900.00 | 8 % |
| above 30,000.00 | 4,100.00 | 6 % |

The norm prevents an applicant from passing affordability by under-declaring expenses. Values are stored as configuration, not hard-coded.

### 4.2 Outcome rules (evaluated in order; every rule result is stored)

| Rule | Condition | Effect |
|---|---|---|
| R0 Data completeness | No income records, `G = 0`, or expenses never captured | `INSUFFICIENT_DATA` (stop) |
| R1 Non-negative residual | `RI < 0` | `UNAFFORDABLE` |
| R2 DTI ceiling | `DTI > max_dti` | `UNAFFORDABLE` |
| R3 Buffer | `0 ≤ RI < Buf` | `MARGINAL` |
| R4 DTI warning | `warn_dti < DTI ≤ max_dti` | `MARGINAL` |
| — | none of the above | `AFFORDABLE` |

The outcome is the worst result across rules (`UNAFFORDABLE` > `MARGINAL` > `AFFORDABLE`). It is **input to a human reviewer**, not a decision.

### 4.3 Worked example C

Product: 21.5 % p.a., service fee 69.00, `max_dti 0.45`, `warn_dti 0.35`, buffer `max(1,500.00, 10 % of N)`. Request: 30,000.00 over 24 months.

| Item | Value |
|---|---:|
| `G` gross | 32,000.00 |
| `N` net | 25,400.00 |
| `E_d` declared expenses | 9,500.00 |
| `E_n` norm = 4,100 + 6 % × (32,000 − 30,000) | 4,220.00 |
| `E` applied = max | 9,500.00 |
| `C` existing debt (vehicle 2,700 + card 500) | 3,200.00 |
| `DI` = 25,400 − 9,500 − 3,200 | 12,700.00 |
| `I` = 1,548.95 + 69.00 | 1,617.95 |
| `RI` = 12,700 − 1,617.95 | 11,082.05 |
| `DTI` = 4,817.95 / 32,000 | 0.1506 |
| `RB` = 4,817.95 / 25,400 | 0.1897 |
| `Buf` = max(1,500, 2,540) | 2,540.00 |
| **Outcome** | **AFFORDABLE** |

## 5. Repayments and payment allocation

Repayments are simulated (no payment provider). Each repayment has an amount, a value date and an idempotency key.

**Allocation waterfall** — applied to instalments oldest-first:

1. Outstanding charges (late fees), oldest first
2. Overdue service fees
3. Overdue interest
4. Overdue principal
5. Current-period service fee, interest, principal (if due date ≤ value date)
6. **Excess** → prepayment of principal

**Prepayment handling:** excess reduces outstanding principal; a new schedule version is generated from the next instalment with the **same instalment amount** and a shorter term (`reason = PREPAYMENT_RECAST`). The previous schedule is retained for history. *(Alternative — keep term, reduce instalment — is documented but not implemented in v1.)*

Interest is schedule-based: paying early or late within a period does not change that period's interest. This is a deliberate simplification.

## 6. Arrears and late payments

- An instalment becomes `OVERDUE` when `today > due_date` and it is not fully paid.
- **Days past due** `DPD = today − due_date of the oldest unpaid instalment` (0 if none).
- **Arrears amount** = Σ unpaid `total_due` of overdue instalments + unpaid charges.
- **Late fee:** if an instalment remains unpaid `grace_period_days` after its due date, one `LATE_FEE` of the product's fixed amount is charged (unique per instalment — never charged twice). No penalty interest in v1.
- **Buckets** for reporting: `CURRENT`, `1–30`, `31–60`, `61–90`, `90+` DPD.
- Loan status is `IN_ARREARS` while `DPD > 0`, back to `ACTIVE` once cleared.
- The arrears job takes the date as an explicit argument (or an injected clock) so tests and demos can simulate the passage of time ("late payment simulation").

## 7. Early settlement quote

For a settlement date `d` (on or after the last paid due date `t`):

```
accrued_interest = q( outstanding_principal × R × (d − t).days / 365 )
settlement_fee   = q( outstanding_principal × early_settlement_fee_rate )
settlement       = outstanding_principal + accrued_interest
                 + arrears (unpaid interest, principal, service fees due ≤ d)
                 + unpaid charges + settlement_fee
```

A quote is valid for the date it is generated for. Paying the quoted amount on that date settles the loan (`SETTLED`), and any remaining scheduled instalments are cancelled in the current schedule version.

Example (from worked example B): after instalment 3 the balance is 26,910.96; settling 17 days later accrues `q(26,910.96 × 0.215 × 17 / 365) = 269.48` interest before fees.

## 8. Portfolio figures

| Metric | Definition |
|---|---|
| Approval rate | approved ÷ (approved + declined), for decisions in the period |
| Total principal disbursed | Σ `principal` of loan accounts disbursed in the period |
| Outstanding principal | Σ cached `outstanding_principal` of `ACTIVE` + `IN_ARREARS` accounts (reconciled nightly against the transaction log) |
| Arrears rate | outstanding principal of `IN_ARREARS` accounts ÷ total outstanding principal |
| Collection rate | Σ repayments received ÷ Σ amounts due, for instalments due in the period |

Portfolio totals are summed per currency and never across currencies.
