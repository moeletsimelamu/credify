"""Fixed settings shared by generation and training. Changing any of them changes results."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

GENERATOR_VERSION = "synthgen-1.0.0"
TRAINER_VERSION = "trainer-1.0.0"
DEFAULT_SEED = 20261118
DEFAULT_ROWS = 50_000

ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"  # git-ignored: reproducible from the seed
ARTEFACTS_DIR = ML_DIR / "artefacts"  # git-ignored: reproducible from the dataset
REPORTS_DIR = ML_DIR / "reports"  # committed

BORROWERS_FILE = "borrowers.parquet"
AUDIT_FILE = "audit_attributes.parquet"
MANIFEST_FILE = "manifest.json"

LABEL = "defaulted_12m"
ID = "borrower_id"

# Every synthetic loan is priced as the demo PERSONAL_UNSECURED product, so the instalment and
# the affordability figures come from exactly the code that serves real applications.
PRODUCT_CODE = "PERSONAL_UNSECURED"
PRODUCT_TERMS: dict[str, Decimal] = {
    "annual_interest_rate": Decimal("0.215"),
    "initiation_fee": Decimal("0.00"),
    "monthly_service_fee": Decimal("69.00"),
    "min_amount": Decimal("5000.00"),
    "max_amount": Decimal("150000.00"),
    "max_dti": Decimal("0.4500"),
    "warn_dti": Decimal("0.3500"),
    "min_residual_amount": Decimal("1500.00"),
    "min_residual_rate": Decimal("0.1000"),
}
SCHEDULE_START = date(2026, 1, 1)  # fixes due dates only; it never changes an amount

# Stratified train / validation / test shares. The test set is used once, in Phase 7.
SPLIT = (0.70, 0.15, 0.15)
CV_FOLDS = 5
