"""Register the evaluated model as a CANDIDATE (docs/ML_STRATEGY.md §9).

    python -m credify_ml.register [--store artefacts/registry]

Copies the packaged artefact into the model store (the directory the API mounts read-only as
``CREDIFY_MODEL_DIR``) and records a ``model_versions`` row with its SHA-256, feature schema,
metrics, threshold, bands, explainer, dataset hash, seed and git commit. Uses the backend's
``CREDIFY_DATABASE_URL``.

Registration never activates a model. An administrator does that through
``POST /api/v1/models/{id}/activate``, which verifies the artefact and is audited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import app.db_models  # noqa: F401 — register every model so foreign keys resolve
from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.modules.credit_risk import registry

from credify_ml.config import ARTEFACTS_DIR, MODEL_NAME, MODEL_VERSION

DEFAULT_STORE = ARTEFACTS_DIR / "registry"


class RegistrationError(RuntimeError):
    pass


def stage(artefacts_dir: Path, store: Path) -> tuple[dict[str, Any], str]:
    """Verify the packaged artefact and copy it into the store. Returns (metadata, uri)."""
    source = artefacts_dir / f"{MODEL_NAME}-{MODEL_VERSION}"
    metadata: dict[str, Any] = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    data = (source / metadata["artefact_file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != metadata["artefact_sha256"]:
        raise RegistrationError("the packaged artefact does not match its metadata; re-evaluate")
    uri = f"{metadata['name']}/{metadata['version']}/model.joblib"
    target = registry.artefact_path(store, uri)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != data:
        raise RegistrationError(f"{uri} already exists in the store with different contents")
    shutil.copyfile(source / metadata["artefact_file"], target)
    return metadata, uri


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register the evaluated model as a CANDIDATE.")
    parser.add_argument("--artefacts", type=Path, default=ARTEFACTS_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args(argv)
    metadata, uri = stage(args.artefacts, args.store)
    engine = build_engine(get_settings().database_url_str)
    try:
        with build_session_factory(engine)() as session:
            version = registry.register(
                session, metadata, artefact_uri=uri, now=SystemClock().now()
            )
            session.commit()
            print(f"Registered {version.name} {version.version} as CANDIDATE (id {version.id}).")
    finally:
        engine.dispose()
    print(f"Artefact: {args.store / uri}")
    print(f"SHA-256:  {metadata['artefact_sha256']}")
    print("Activate it as an administrator: POST /api/v1/models/{id}/activate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
