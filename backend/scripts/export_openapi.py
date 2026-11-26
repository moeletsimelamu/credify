"""Write the API's OpenAPI document for the frontend's generated types.

    uv run python -m scripts.export_openapi            # writes ../frontend/openapi.json
    uv run python -m scripts.export_openapi --check    # fails if the committed copy is stale

No database connection is made: the schema comes from the route and model definitions only,
so placeholder settings are enough.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def build_schema() -> dict[str, Any]:
    settings = Settings(
        environment="development",
        database_url="postgresql+psycopg://openapi:unused@127.0.0.1:1/openapi",  # never opened
        jwt_secret="openapi-export-" + "x" * 32,
        log_json=False,
        log_level="WARNING",
    )
    return create_app(settings).openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args(argv)

    text = render(build_schema())
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            print(f"{args.output} is out of date; run: uv run python -m scripts.export_openapi")
            return 1
        print("OpenAPI document is up to date.")
        return 0
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
