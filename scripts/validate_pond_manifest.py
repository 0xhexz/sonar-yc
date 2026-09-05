"""Validate OUR manifest against Pond's OWN published schema (vendored from Foxy's repo).

Foxy's commits revealed Pond has a strict schema with additionalProperties=false —
a manifest can answer 200 on every endpoint and still be silently rejected by Pond.
This script closes that gap for yc-radar. Run: python3 scripts/validate_pond_manifest.py
Optionally fetches Pond's schema fresh; falls back to the vendored copy.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("RUN_ON_START", "false")

VENDORED = Path(__file__).resolve().parents[1] / "tests" / "data" / "pond-manifest-schema.json"
POND_SCHEMA_URL = "https://raw.githubusercontent.com/ana-momin/Foxy/main/tests/data/pond-manifest-schema.json"


def load_schema() -> dict:
    try:
        with urllib.request.urlopen(POND_SCHEMA_URL, timeout=15) as r:
            return json.load(r)
    except Exception as exc:  # noqa: BLE001
        print(f"(vendored copy used — fetch failed: {exc})")
        return json.loads(VENDORED.read_text())


def main() -> int:
    import jsonschema
    from fastapi.testclient import TestClient

    from app.main import app

    m = TestClient(app).get("/manifest").json()
    schema = load_schema()
    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    if not VENDORED.exists():
        VENDORED.write_text(json.dumps(schema, indent=2))

    errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(m), key=lambda e: list(e.path))
    if not errs:
        print("PASS: manifest is 100% valid against Pond's published schema")
        return 0
    print(f"FAIL: {len(errs)} schema violation(s):")
    for e in errs[:15]:
        print(" -", list(e.path), "->", e.message[:150])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
