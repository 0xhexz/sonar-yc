"""Configuration for YC Radar.

All values are loaded from environment variables / a local ``.env`` file
via pydantic-settings. Every external dependency (X provider, LinkedIn
provider, Slack, Pond) is config-driven so an API key can be swapped later
without touching code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Monitoring cadence (per-source, editable via .env) -----------------
    yc_interval_hours: int = Field(default=8, ge=1, description="YC directory poll cadence (hours)")
    speedrun_interval_hours: int = Field(default=8, ge=1, description="Speedrun directory poll cadence (hours)")
    x_interval_minutes: int = Field(default=30, ge=5, description="X poll cadence (minutes, min 5)")
    linkedin_interval_hours: int = Field(default=24, ge=1, description="LinkedIn poll cadence (hours; each scan costs ~$0.12)")
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
    slack_channel: str = Field(default="#yc-radar", description="Channel to post alerts to")
    slack_dm_user: str | None = Field(
        default=None, description="If set, DM this user instead of a channel"
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
    classify_batch_size: int = Field(default=6, description="Posts per LLM classification batch (small for small-context models)")
    social_max_alerts_per_scan: int = Field(
        default=8, description="Cap on social alerts per scan (burst protection); rest are recorded silently"
    )

    # ---- Data / misc -------------------------------------------------------
    data_dir: str = Field(default="data", description="Directory for state.db")
    timezone: str = Field(default="America/Los_Angeles", description="PT for alert timestamps")
    yc_batches: str = Field(default="", description="Optional YC batch filter (comma list)")
    http_timeout: int = Field(default=30, description="Seconds")

    # ---- Derived helpers ---------------------------------------------------
    @field_validator("sources_enabled", "x_keywords", "linkedin_keywords", mode="before")
    @classmethod
    def _ensure_str(cls, v):
        return "," if v is None else v

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
