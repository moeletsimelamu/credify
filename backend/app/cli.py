"""Operational commands.

Bootstrap the first administrator (staff accounts cannot self-register)::

    CREDIFY_ADMIN_PASSWORD=... python -m app.cli create-admin --email admin@example.com \
        --full-name "Platform Admin"

If CREDIFY_ADMIN_PASSWORD is unset the password is prompted for interactively.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from pydantic import ValidationError

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.core.errors import DomainError
from app.modules.audit import service as audit
from app.modules.identity import service
from app.modules.identity.models import Role
from app.modules.identity.schemas import UserCreate


def _read_password() -> str:
    from_env = os.environ.get("CREDIFY_ADMIN_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass("Password (min 12 characters): ")
    if first != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    return first


def create_admin(email: str, full_name: str) -> int:
    try:
        data = UserCreate(
            email=email, full_name=full_name, role=Role.ADMIN, password=_read_password()
        )
    except ValidationError as exc:
        print(f"Invalid input: {exc.errors(include_input=False)}", file=sys.stderr)
        return 2
    engine = build_engine(get_settings().database_url_str)
    clock = SystemClock()
    try:
        with build_session_factory(engine)() as session:
            user = service.create_user_record(
                session,
                email=data.email,
                password=data.password,
                full_name=data.full_name,
                role=Role.ADMIN,
            )
            audit.record(
                session,
                action="user.created",
                entity_type="user",
                entity_id=user.id,
                occurred_at=clock.now(),
                context={"source": "cli.create-admin"},
            )
            session.commit()
            print(f"Created administrator {user.email} ({user.id})")
            return 0
    except DomainError as exc:
        print(f"Error: {exc.detail}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="Create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--full-name", required=True)
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin(args.email, args.full_name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
