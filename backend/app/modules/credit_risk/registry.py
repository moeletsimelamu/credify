"""Model registry and hash-verified loading (docs/ML_STRATEGY.md §9).

joblib (pickle) can run code while loading, so a model file is only ever loaded:

* from the configured, read-only model directory (``CREDIFY_MODEL_DIR``), never a path or
  URL supplied by a request — there is no upload endpoint;
* after its bytes match the SHA-256 recorded when it was registered, checked on the very
  bytes that are then deserialised (no gap between checking and loading); and
* when it was trained with the same scikit-learn version the API runs.

Registration creates a ``CANDIDATE``. Activation is a separate, audited admin action that
retires the previously active version of the same model.
"""

from __future__ import annotations

import hashlib
import io
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import joblib
import sklearn
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.ids import uuid7
from app.modules.audit import service as audit
from app.modules.credit_risk import features as shared
from app.modules.credit_risk.explain import EXPLAINER_KIND
from app.modules.credit_risk.models import ModelStatus, ModelVersion
from app.modules.identity.models import User

DEFAULT_MODEL_NAME = "credify-pd"


class ModelIntegrityError(RuntimeError):
    """The artefact is missing, outside the model directory, altered or incompatible."""


def artefact_path(model_dir: Path, artefact_uri: str) -> Path:
    """Resolve a registered artefact inside ``model_dir``, refusing anything that escapes it."""
    relative = PurePosixPath(artefact_uri)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ModelIntegrityError(f"artefact path {artefact_uri!r} is not a plain relative path")
    root = model_dir.resolve()
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise ModelIntegrityError(f"artefact path {artefact_uri!r} escapes the model directory")
    return path


def verified_bytes(model_dir: Path, version: ModelVersion) -> bytes:
    path = artefact_path(model_dir, version.artefact_uri)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ModelIntegrityError(f"artefact {version.artefact_uri} cannot be read") from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != version.artefact_sha256:
        raise ModelIntegrityError(
            f"artefact {version.artefact_uri} has SHA-256 {digest[:12]}…, expected "
            f"{version.artefact_sha256[:12]}…; refusing to load it"
        )
    trained_with = str(version.libraries.get("scikit-learn"))
    if trained_with != sklearn.__version__:
        raise ModelIntegrityError(
            f"model was trained with scikit-learn {trained_with}; the API runs "
            f"{sklearn.__version__}"
        )
    return data


@dataclass(frozen=True, slots=True)
class LoadedModel:
    version_id: uuid.UUID
    sha256: str
    pipeline: Any


_cache: dict[tuple[uuid.UUID, str], LoadedModel] = {}
_lock = threading.Lock()


def load(model_dir: Path | None, version: ModelVersion) -> LoadedModel:
    """The verified, deserialised pipeline for ``version`` (cached per id and hash)."""
    if model_dir is None:
        raise ModelIntegrityError("no model directory is configured (CREDIFY_MODEL_DIR)")
    key = (version.id, version.artefact_sha256)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        data = verified_bytes(model_dir, version)
        pipeline = joblib.load(io.BytesIO(data))
        names = list(getattr(pipeline, "feature_names_in_", []))
        if names != list(shared.FEATURE_NAMES):
            raise ModelIntegrityError("the artefact was trained on a different feature list")
        loaded = LoadedModel(version.id, version.artefact_sha256, pipeline)
        _cache[key] = loaded
        return loaded


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# --- Registration and activation ---------------------------------------------------------


def _schema_problems(schema: Mapping[str, Any]) -> list[str]:
    problems = []
    expected = shared.schema()
    if schema.get("version") != expected["version"]:
        problems.append(
            f"feature schema {schema.get('version')} does not match {expected['version']}"
        )
    names = [f.get("name") for f in schema.get("features", [])]
    if names != list(shared.FEATURE_NAMES):
        problems.append("the model's features differ from the shared feature builder")
    leaked = sorted(set(names) & shared.PROTECTED_ATTRIBUTES)
    if leaked:
        problems.append(f"protected attributes cannot be model features: {leaked}")
    return problems


def register(
    session: Session,
    metadata: Mapping[str, Any],
    *,
    artefact_uri: str,
    now: datetime,
    actor: User | None = None,
) -> ModelVersion:
    """Record a packaged model as a CANDIDATE (no commit: the caller's transaction)."""
    problems = _schema_problems(metadata["feature_schema"])
    if metadata.get("explainer", {}).get("kind") != EXPLAINER_KIND:
        problems.append("the model must carry an exact linear explainer")
    artefact_path(Path("."), artefact_uri)  # shape check only; existence is checked on use
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    exists = session.scalar(
        select(ModelVersion.id).where(
            ModelVersion.name == metadata["name"], ModelVersion.version == metadata["version"]
        )
    )
    if exists:
        raise ConflictError(f"{metadata['name']} {metadata['version']} is already registered")
    dataset = metadata["dataset"]
    version = ModelVersion(
        id=uuid7(),
        name=metadata["name"],
        version=metadata["version"],
        algorithm=metadata["algorithm"],
        artefact_uri=artefact_uri,
        artefact_sha256=metadata["artefact_sha256"],
        feature_schema=dict(metadata["feature_schema"]),
        training_dataset_sha256=dataset["content_sha256"],
        dataset_generator_version=dataset["generator_version"],
        random_seed=int(dataset["seed"]),
        metrics=dict(metadata["metrics"]),
        decision_threshold=Decimal(str(metadata["decision_threshold"])),
        band_cutoffs=dict(metadata["band_cutoffs"]),
        explainer=dict(metadata["explainer"]),
        libraries=dict(metadata["libraries"]),
        status=ModelStatus.CANDIDATE,
        git_commit=metadata.get("git_commit"),
        trained_at=datetime.fromisoformat(metadata["trained_at"]),
        created_at=now,
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        action="model_version.registered",
        entity_type="model_version",
        entity_id=version.id,
        actor=actor,
        occurred_at=now,
        context={
            "name": version.name,
            "version": version.version,
            "artefact_sha256": version.artefact_sha256,
            "training_dataset_sha256": version.training_dataset_sha256,
        },
    )
    return version


def activate(
    session: Session,
    model_id: uuid.UUID,
    *,
    model_dir: Path | None,
    actor: User,
    now: datetime,
    ip_address: str | None,
) -> ModelVersion:
    """CANDIDATE → ACTIVE (the previous ACTIVE version → RETIRED). Verifies the artefact first."""
    version = session.scalar(
        select(ModelVersion).where(ModelVersion.id == model_id).with_for_update()
    )
    if version is None:
        raise NotFoundError("Model version not found")
    if version.status is not ModelStatus.CANDIDATE:
        raise ConflictError(
            f"Only a CANDIDATE can be activated; this one is {version.status.value}"
        )
    try:
        load(model_dir, version)
    except ModelIntegrityError as exc:
        raise BusinessRuleError(f"The artefact failed verification: {exc}") from exc
    previous = session.scalars(
        select(ModelVersion)
        .where(ModelVersion.name == version.name, ModelVersion.status == ModelStatus.ACTIVE)
        .with_for_update()
    ).all()
    for old in previous:
        old.status = ModelStatus.RETIRED
        old.retired_at = now
    session.flush()  # release the one-active index before activating the new version
    version.status = ModelStatus.ACTIVE
    version.activated_at = now
    session.flush()
    audit.record(
        session,
        action="model_version.activated",
        entity_type="model_version",
        entity_id=version.id,
        actor=actor,
        occurred_at=now,
        ip_address=ip_address,
        changes={"status": {"from": ModelStatus.CANDIDATE.value, "to": ModelStatus.ACTIVE.value}},
        context={
            "name": version.name,
            "version": version.version,
            "retired": [str(old.id) for old in previous],
            "artefact_sha256": version.artefact_sha256,
        },
    )
    session.commit()
    return version


def active_version(session: Session, name: str = DEFAULT_MODEL_NAME) -> ModelVersion | None:
    return session.scalar(
        select(ModelVersion).where(
            ModelVersion.name == name, ModelVersion.status == ModelStatus.ACTIVE
        )
    )


def list_versions(session: Session) -> Sequence[ModelVersion]:
    return session.scalars(
        select(ModelVersion).order_by(ModelVersion.name, ModelVersion.created_at.desc())
    ).all()


def get_version(session: Session, model_id: uuid.UUID) -> ModelVersion:
    version = session.get(ModelVersion, model_id)
    if version is None:
        raise NotFoundError("Model version not found")
    return version
