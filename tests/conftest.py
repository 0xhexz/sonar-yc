"""Test-wide hermetic configuration.

MUST run before any app import: neutralises .env so tests never kick off
real network scans (Sorsa/Speedrun/Playwright) from the app lifespan.
Individual env vars take priority over the .env file in pydantic-settings,
so setting them here makes the suite fully hermetic.
"""
import os
import tempfile

os.environ["RUN_ON_START"] = "false"
os.environ["SOURCES_ENABLED"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ycradar_test_")
os.environ["X_PROVIDER_API_KEY"] = ""
os.environ["LINKEDIN_PROVIDER_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""
