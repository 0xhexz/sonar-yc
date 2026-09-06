"""YC Radar — FastAPI app: monitor scheduler + Pond Protocol V1 endpoints.

The service:
* runs the monitoring loop on schedule (directory sources every
  ``DIRECTORY_INTERVAL_HOURS``, social sources every ``SOCIAL_INTERVAL_MINUTES``),
* exposes Pond Protocol V1 endpoints so Pond can discover, call and health-check
  the agent (``/manifest`` public; ``/runs`` and ``/tasks`` access-key protected),
* exposes an admin ``POST /trigger`` for a manual scan.

Run: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``
"""
from __future__ import annotations

import logging
import re
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import get_settings
from .loop import run_scan
from .slack_notifier import SlackNotifier
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ycradar")

APP_VERSION = "1.0.0"

# ---- application state -----------------------------------------------------
store: Store = None  # type: ignore[assignment]
notifier: SlackNotifier = None  # type: ignore[assignment]
scheduler = None
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, notifier, scheduler
    store = Store(_settings.state_db_path)
    notifier = SlackNotifier(
        bot_token=_settings.slack_bot_token,
        webhook_url=_settings.slack_webhook_url,
        channel=_settings.slack_channel,
        dm_user=_settings.slack_dm_user,
    )

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    # YC Directory — own interval.
    scheduler.add_job(
        _yc_job,
        "interval",
        hours=_settings.yc_interval_hours,
        id="yc",
        max_instances=1,
        coalesce=True,
    )
    # Speedrun — own interval.
    scheduler.add_job(
        _speedrun_job,
        "interval",
        hours=_settings.speedrun_interval_hours,
        id="speedrun",
        max_instances=1,
        coalesce=True,
    )
    # X (fast, cheap via TwtAPI free tier) — own interval.
    scheduler.add_job(
        _x_job,
        "interval",
        minutes=_settings.x_interval_minutes,
        id="x",
        max_instances=1,
        coalesce=True,
    )
    # LinkedIn (Apify per-post cost) — own interval (default daily).
    scheduler.add_job(
        _linkedin_job,
        "interval",
        hours=_settings.linkedin_interval_hours,
        id="linkedin",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    if _settings.run_on_start:
        import asyncio

        logger.info("run_on_start: kicking off an initial full scan")
        asyncio.create_task(scan_now())

    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    store.close()


async def _yc_job():
    await scan_now(only=["yc"])


async def _speedrun_job():
    await scan_now(only=["speedrun"])


async def _x_job():
    await scan_now(only=["x"])


async def _linkedin_job():
    await scan_now(only=["linkedin"])


async def scan_now(only: list[str] | None = None):
    try:
        return await run_scan(_settings, store, notifier, only=only)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan failed: %s", exc)
        return None


app = FastAPI(title="YC Radar", version=APP_VERSION, lifespan=lifespan)


@app.get("/")
async def root():
    """Serve the SONAR landing page; JSON pointer if the page is missing."""
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    static = Path(__file__).parent / "static" / "index.html"
    if static.exists():
        return HTMLResponse(static.read_text(encoding="utf-8"))
    return {"name": "SONAR", "version": APP_VERSION, "ok": True}


@app.get("/dashboard")
async def dashboard():
    """Human-readable window into the monitor: counters, health, recents."""
    from fastapi.responses import HTMLResponse

    from .dashboard import _find_store, render_dashboard

    db = store or _find_store()
    return HTMLResponse(render_dashboard(db))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "last_scan_at": store.get_state("last_scan_at"),
        "playwright_ok": _is_playwright_ok(),
    }


def _is_playwright_ok() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


# ---- admin -----------------------------------------------------------------
class TriggerBody(BaseModel):
    only: list[str] | None = None


@app.post("/trigger")
async def trigger(body: TriggerBody | None = None):
    """Admin endpoint to run a scan now (e.g. for local testing)."""
    result = await scan_now(only=(body.only if body else None))
    if result is None:
        return {"ok": False}
    return {"ok": True, "alerts": len(result.alerts), "counts": result.counts}


# ---- Pond Protocol V1 ------------------------------------------------------
def _fail(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


@app.get("/manifest")
async def manifest():
    """Public — must succeed without an access key or version header."""
    return {
        "protocol": "marketplace-agent",
        "protocol_version": "1.0",
        "agent_version": APP_VERSION,
        "metadata": {
            "name": "SONAR",
            "logo_url": "https://sonar-yc.vercel.app/logo.png",
            "short_description": "Monitors YC + a16z Speedrun launches and founders who announce on X/LinkedIn before the official announcement.",
            "description": (
                "<p>YC Radar polls the YC directory, the a16z Speedrun directory, X and LinkedIn "
                "for new YC/Speedrun companies and fires Slack alerts. It specialises in early "
                "detection: a founder posting about their acceptance before the accelerator "
                "announces it.</p>"
            ),
            "category": "productivity",
            "key_features": "<ul><li>YC Directory</li><li>Speedrun (a16z)</li><li>X</li><li>LinkedIn</li><li>Early-detection</li></ul>",
            "use_cases": "<p>GTM pipeline-building: reach YC/Speedrun founders the moment they announce.</p>",
            "setup_instructions": "Configure env (providers, Slack, Pond) and deploy. POST /runs with action scan_now to trigger a monitor scan.",
            "developer_x_url": "",
            "github_url": "",
        },
        "actions": [
            {
                "id": "scan_now",
                "name": "Run Monitor Scan",
                "description": "Run one YC/Speedrun monitoring scan and return a summary of alerts.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "only": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["yc", "speedrun", "x", "linkedin", "hn"],
                                "description": "Source name.",
                            },
                            "description": "Optional subset of sources to scan.",
                        }
                    },
                    "additionalProperties": False,
                },
            }
        ],
        "capabilities": {"sync": True, "streaming": False, "async_tasks": False,
                         "cancellation": False, "attachments": False, "feedback": False},
        "input_modes": ["application/json"],
        "output_modes": ["text/markdown"],
        "limits": {
            "max_request_bytes": 1_048_576,
            "max_run_seconds": 600,
            "max_attachment_bytes": 1_048_576,
        },
    }


class RunRequest(BaseModel):
    run_id: str
    agent_id: str
    conversation_id: str
    history_truncated: bool = False
    action_id: str | None = None
    user: dict = {}
    messages: list[dict] = []
    parameters: dict = {}
    execution: dict = {}


def _auth_pond(authorization: str | None = Header(default=None),
               pond_version: str | None = Header(default=None, alias="X-Agent-Protocol-Version")):
    key = _settings.pond_access_key
    if authority := authorization:
        if key and authority != f"Bearer {key}":
            _fail(401, "unauthorized", "The Access Key is missing or invalid.")
    else:
        if key:
            _fail(401, "unauthorized", "Access Key required.")
    if pond_version is None or re.fullmatch(r"\d+\.\d+", pond_version) is None:
        _fail(400, "invalid_request", "The protocol version must be Major.Minor.")
    if pond_version != "1.0":
        _fail(400, "unsupported_protocol_version", f"Protocol version {pond_version} is not supported.")


@app.post("/runs", dependencies=[Depends(_auth_pond)])
async def create_run(run: RunRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if idempotency_key != run.run_id:
        _fail(400, "invalid_request", "Idempotency-Key must match run_id.")
    if run.action_id not in (None, "scan_now"):
        _fail(400, "unsupported_operation", "The action is not supported.")

    only = run.parameters.get("only") if isinstance(run.parameters, dict) else None
    if isinstance(only, list) and only:
        only = [s for s in only if s in ("yc", "speedrun", "x", "linkedin")] or None
    else:
        only = None

    result = await scan_now(only=only)
    if result is None:
        return {
            "run_id": run.run_id,
            "status": "failed",
            "error": {"code": "internal_error", "message": "The scan failed."},
            "usage": {"unit_of_measurement": "scan", "quantity": 0},
        }

    summary = f"Scan complete. {len(result.alerts)} alert(s): " + ", ".join(
        f"{a.classification}:{a.company_name}" for a in result.alerts
    ) or "Scan complete. No new alerts."

    return {
        "run_id": run.run_id,
        "status": "completed",
        "output": [{"type": "text", "text": summary}],
        "usage": {"unit_of_measurement": "scan", "quantity": 1},
    }


# ---- Optional Pond Protocol: tasks endpoint -------------------------------
# capabilities.async_tasks is False, so Pond never polls this — but its
# conformance checker probes that the route exists on a spec-shaped server,
# so we implement it exactly per the Integration Guide and back it with a
# persistent store so a real async mode could adopt it unchanged.
@app.get("/tasks/{task_id}", dependencies=[Depends(_auth_pond)])
async def get_task(task_id: str):
    stored = _task_store().get(task_id)
    if not stored:
        _fail(404, "not_found", f"Unknown task_id: {task_id}")
    return {
        "run_id": stored["run_id"],
        "task_id": task_id,
        "status": stored["status"],
        "output": stored["output"],
        "usage": stored["usage"],
        "updated_at": stored["updated_at"],
    }


_TASK_STORE_PATH = Path(__file__).parent / "tasks_state.json"


def _task_store() -> dict:
    """Tiny persistent task registry (JSON file). Only written by async runs,
    which are disabled today; the endpoint reads it so the contract holds."""
    try:
        return json.loads(_TASK_STORE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


# ---- Pond error responses --------------------------------------------------
@app.exception_handler(HTTPException)
async def pond_error(_request: Request, error: HTTPException):
    return JSONResponse(status_code=error.status_code, content={"error": error.detail})
