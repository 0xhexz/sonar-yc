"""Configuration for YC Radar.

All values are loaded from environment variables / a local ``.env`` file
via pydantic-settings. Every external dependency (X provider, LinkedIn
provider, Slack, Pond) is config-driven so an API key can be swapped later
without touching code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any
import os

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_duration_seconds(v: int | float | str, default_unit: str = "h") -> int:
    """Parse a flexible duration into total seconds.

    Supports:
    - Pure numbers: int or float or string digits (interpreted using `default_unit`,
      e.g. 2 -> 2h -> 7200s, 30 -> 30m -> 1800s)
    - Duration strings with units: '30m', '30 min', '8h', '1d', '0.5h', '45s', '1h30m'
    """
    if isinstance(v, (int, float)):
        mult = 3600 if default_unit == "h" else 60
        return max(1, int(v * mult))

    v_str = str(v).strip().lower()
    if not v_str:
        mult = 3600 if default_unit == "h" else 60
        return mult

    if re.fullmatch(r"\d+", v_str):
        mult = 3600 if default_unit == "h" else 60
        return max(1, int(v_str) * mult)
    if re.fullmatch(r"\d+\.\d+", v_str):
        mult = 3600 if default_unit == "h" else 60
        return max(1, int(float(v_str) * mult))

    units = {
        "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
        "d": 86400, "day": 86400, "days": 86400,
    }
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?", v_str)
    if not matches:
        raise ValueError(f"Invalid duration string format: {v!r}")

    total = 0.0
    for num_str, unit_str in matches:
        u_clean = unit_str.lower() if unit_str else default_unit
        if u_clean not in units:
            raise ValueError(f"Unknown duration unit {unit_str!r} in {v!r}")
        total += float(num_str) * units[u_clean]

    return max(1, int(round(total)))


def format_duration(seconds: int | float) -> str:
    """Format total seconds into human-readable duration string (e.g. '30m', '8h', '1d', '1h30m')."""
    sec = int(round(seconds))
    if sec <= 0:
        return "0s"
    days = sec // 86400
    rem = sec % 86400
    hours = rem // 3600
    rem = rem % 3600
    minutes = rem // 60
    rem_s = rem % 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if rem_s:
        parts.append(f"{rem_s}s")
    return "".join(parts) if parts else f"{sec}s"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Monitoring cadence (per-source, editable via .env) -----------------
    yc_interval_hours: int | float | str = Field(
        default=8,
        validation_alias=AliasChoices("yc_interval_hours", "yc_interval"),
        description="YC directory poll cadence (hours or duration string like '8h', '30m', '1d', '0.5h')",
    )
    speedrun_interval_hours: int | float | str = Field(
        default=8,
        validation_alias=AliasChoices("speedrun_interval_hours", "speedrun_interval"),
        description="Speedrun directory poll cadence (hours or duration string like '8h', '30m', '1d', '0.5h')",
    )
    x_interval_minutes: int | float | str = Field(
        default=30,
        validation_alias=AliasChoices("x_interval_minutes", "x_interval"),
        description="X poll cadence (minutes or duration string like '30m', '1h', '0.5h')",
    )
    linkedin_interval_hours: int | float | str = Field(
        default=24,
        validation_alias=AliasChoices("linkedin_interval_hours", "linkedin_interval"),
        description="LinkedIn poll cadence (hours or duration string like '24h', '1d', '12h')",
    )
    linkedin_max_posts: int = Field(
        default=15, description="Max posts per keyword per Apify run (cost control)"
    )
    run_on_start: bool = Field(default=True, description="Run one scan immediately on boot")
    sources_enabled: str = Field(
        default="yc,speedrun,x,linkedin",
        description="Comma list of enabled sources",
    )

    # ---- X provider --------------------------------------------------------
    x_provider_base_url: str | None = Field(
        default=None, description="Third-party X API base URL (e.g. twitterapi.io)"
    )
    x_provider_api_key: str | None = Field(default=None)
    x_keywords: str = Field(
        default=(
            '"Y Combinator","got into YC","YC S26","backed by Y Combinator",'
            '"accepted to YC","YC batch","a16z speedrun"'
        ),
        description="Comma-separated keyword set for X search",
    )
    x_provider_search_path: str = Field(
        default="/twitter/tweet/advanced_search",
        description="Provider search endpoint path (relative to base URL)",
    )
    x_lang: str = Field(default="en", description="Restrict X results to this language")
    serper_api_key: str | None = Field(
        default=None,
        description="Serper.dev key (2,500 free one-off credits) for the free-X discovery chain",
    )

    # ---- Hacker News (free founder-signal source) ---------------------------
    hn_enabled: bool = Field(
        default=True,
        description="Poll Hacker News (Algolia API, free) for 'Launch HN (YC ...)' founder posts",
    )

    # ---- LinkedIn provider -------------------------------------------------
    linkedin_provider_base_url: str | None = Field(default=None)
    linkedin_provider_api_key: str | None = Field(default=None)
    linkedin_provider_search_path: str = Field(
        default="/api/posts/search",
        description="Provider post-search endpoint path (relative to base URL)",
    )
    linkedin_provider_actor: str = Field(
        default="apimaestro~linkedin-posts-search-scraper-no-cookies",
        description="Apify actor (username~name) used when the LinkedIn provider is Apify",
    )
    linkedin_keywords: str = Field(
        default='"got into YC","Y Combinator","YC batch","accepted to YC","a16z speedrun"',
        description="Comma-separated keyword set for LinkedIn post search",
    )

    # ---- Slack -------------------------------------------------------------
    slack_bot_token: str | None = Field(default=None, description="xoxb- bot token")
    slack_webhook_url: str | None = Field(default=None, description="Incoming webhook fallback")
    slack_channel: str = Field(
        default="#yc-radar",
        validation_alias=AliasChoices("slack_channel", "slack_channel_id"),
        description="Channel to post alerts to (channel name or channel ID)",
    )
    slack_dm_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("slack_dm_user", "slack_dm_user_id"),
        description="If set, DM this user instead of a channel",
    )

    # ---- Pond --------------------------------------------------------------
    pond_access_key: str | None = Field(default=None, description="Pond runtime Access Key")

    # ---- LLM classifier (Idea 1 — ChirpSieve-style intent filter) ----------
    llm_api_key: str | None = Field(default=None, description="LLM API key (OpenAI-compatible)")
    llm_base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI-compatible base URL")
    llm_model: str = Field(default="gpt-4o-mini", description="Model name for the classifier")
    classify_enabled: bool = Field(default=True, description="Run LLM classification on social signals")
    classify_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    classify_timeout: int = Field(default=30, description="Seconds per LLM classification call")
    classify_batch_size: int = Field(default=6, description="Posts per LLM classification batch (configurable via CLASSIFY_BATCH_SIZE)")
    social_max_alerts_per_scan: int = Field(
        default=8, description="Cap on social alerts per scan (burst protection); rest are recorded silently"
    )

    # ---- Data / misc -------------------------------------------------------
    data_dir: str = Field(default="data", description="Directory for state.db")
    timezone: str = Field(default="America/Los_Angeles", description="PT for alert timestamps")
    yc_batches: str = Field(default="", description="Optional YC batch filter (comma list)")
    http_timeout: int = Field(default=30, description="Seconds")

    # ---- Derived helpers ---------------------------------------------------
    @field_validator("yc_interval_hours", "speedrun_interval_hours", "linkedin_interval_hours", mode="before")
    @classmethod
    def _validate_hour_cadence(cls, v: Any) -> int | float | str:
        if isinstance(v, str):
            v_str = v.strip()
            if re.fullmatch(r"\d+", v_str):
                return int(v_str)
            if re.fullmatch(r"\d+\.\d+", v_str):
                return float(v_str)
        parse_duration_seconds(v, default_unit="h")
        return v

    @field_validator("x_interval_minutes", mode="before")
    @classmethod
    def _validate_minute_cadence(cls, v: Any) -> int | float | str:
        if isinstance(v, str):
            v_str = v.strip()
            if re.fullmatch(r"\d+", v_str):
                return int(v_str)
            if re.fullmatch(r"\d+\.\d+", v_str):
                return float(v_str)
        parse_duration_seconds(v, default_unit="m")
        return v

    @field_validator("sources_enabled", "x_keywords", "linkedin_keywords", mode="before")
    @classmethod
    def _ensure_str(cls, v):
        return "," if v is None else v

    @property
    def yc_interval_seconds(self) -> int:
        return parse_duration_seconds(self.yc_interval_hours, default_unit="h")

    @property
    def speedrun_interval_seconds(self) -> int:
        return parse_duration_seconds(self.speedrun_interval_hours, default_unit="h")

    @property
    def x_interval_seconds(self) -> int:
        return parse_duration_seconds(self.x_interval_minutes, default_unit="m")

    @property
    def linkedin_interval_seconds(self) -> int:
        return parse_duration_seconds(self.linkedin_interval_hours, default_unit="h")

    @property
    def yc_cadence_label(self) -> str:
        return format_duration(self.yc_interval_seconds)

    @property
    def speedrun_cadence_label(self) -> str:
        return format_duration(self.speedrun_interval_seconds)

    @property
    def x_cadence_label(self) -> str:
        return format_duration(self.x_interval_seconds)

    @property
    def linkedin_cadence_label(self) -> str:
        return format_duration(self.linkedin_interval_seconds)

    @property
    def enabled_source_list(self) -> list[str]:
        return [s.strip() for s in self.sources_enabled.split(",") if s.strip()]

    @property
    def x_keyword_list(self) -> list[str]:
        # comma-separated; allow optional surrounding quotes on phrases
        return [k.strip().strip('"').strip() for k in self.x_keywords.split(",") if k.strip()]

    @property
    def linkedin_keyword_list(self) -> list[str]:
        return [
            k.strip().strip('"').strip()
            for k in self.linkedin_keywords.split(",")
            if k.strip()
        ]

    @property
    def state_db_path(self) -> Path:
        return Path(self.data_dir) / "state.db"

    @property
    def is_x_ready(self) -> bool:
        return bool(self.x_provider_base_url and self.x_provider_api_key)

    @property
    def is_linkedin_ready(self) -> bool:
        return bool(self.linkedin_provider_base_url and self.linkedin_provider_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
