# YC Radar 🔭

A persistent, stateful Slack bot that keeps a GTM team ahead of **every new YC
and a16z Speedrun company launch** — including founders who announce on X or
LinkedIn *before* the accelerator has officially announced them.

It monitors **four sources continuously**, de-duplicates against a local SQLite
store, and fires rich Slack alerts the moment something new appears.

> **Powered by:** YC Directory · a16z Speedrun · X · LinkedIn · Slack ·
> Pond Protocol V1.

---

## ✨ What it detects

| Alert type | Header | Trigger |
|---|---|---|
| 🔥 **EARLY YC SIGNAL** | Founder announced *before* YC | A founder posts about being accepted on X/LinkedIn **before** YC confirms |
| ⚡ **NEW YC COMPANY** | Confirmed by YC | YC adds the company to its directory |
| 🚀 **NEW SPEEDRUN COMPANY** | Confirmed — a16z Speedrun | A company appears on the a16z Speedrun directory |

Every alert includes the **company name, batch/cohort, founder, source,
description and a link**, plus a detected timestamp (PT).

---

## 🧠 Why this is different

* **Early detection first.** The pipeline is driven by the *social* sources
  (X/LinkedIn) run on keywords. It never waits for a directory or an official
  announcement — the directory is only used to confirm, or to mark a matched
  founder as "not yet officially announced."
* **No duplicate alerts.** A deduplicated identity key lives in SQLite, so a
  detected company won't be re-alerted. A founder first flagged as EARLY is
  tracked as *pending* and is upgraded to CONFIRMED once it lands in a directory
  (one coherent alert lifecycle).
* **Respectful by design.** YC's `robots.txt` disallows filter queries, so the
  YC source is deliberately minimal, polite, and cached.

---

## 📦 Architecture

```
                    ┌─────────────────────────────────────────────┐
  YC Directory ───▶ │  sources/ (pluggable BaseSource adapters)   │
  a16z Speedrun ─▶ │  ├─ yc_directory   (Playwright render)      │
  X (provider) ───▶│  ├─ speedrun       (public REST API)        │
  LinkedIn ───────▶│  ├─ x              (3rd-party provider)     │
                    │  └─ linkedin       (3rd-party provider)     │
                    └───────────────┬─────────────────────────────┘
                                    ▼
              normalize → CompanySignal → SQLite store (dedupe + pending)
                                    ▼
              classify → EARLY / CONFIRMED / SPEEDRUN
                                    ▼
              SlackNotifier (Block Kit) → Slack channel / DM
                                    ▼
              also served via FastAPI: /manifest /runs /health (Pond)
```

### Cadence
Directories change slowly; social moves fast. The loop runs each at its own
interval (configurable):
* **YC Directory:** every `YC_INTERVAL_HOURS` (default 8h)
* **Speedrun:** every `SPEEDRUN_INTERVAL_HOURS` (default 8h)
* **X:** every `X_INTERVAL_MINUTES` (default 30m)
* **LinkedIn:** every `LINKEDIN_INTERVAL_HOURS` (default 24h — Apify costs per post)

A source failure is logged as a **coverage gap**, never treated as "no news."

---

## 🚀 Quick start (60 seconds)

```bash
git clone <your-repo-url> yc-radar && cd yc-radar

# 1) Python deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # only needed for the YC source

# 2) Configure
cp .env.example .env               # fill in your Slack + provider keys

# 3) Run
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify it's alive: `curl http://localhost:8000/health`

> **No keys yet?** The bot runs in *mock/dry-run* mode — sources without
> credentials return realistic sample signals (including a founder who
> pre-announced YC) so you can see the full pipeline work before paying
> anything.

---

## 🐳 Run with Docker

```bash
docker compose up -d
```
Full walkthrough lives in this README — see Setup above; systemd unit: `scripts/yc-radar.service`.

---

## 🔄 X provider switching (cost guide — measured, not marketing)

The X adapter auto-detects the provider from `X_PROVIDER_BASE_URL`. Your
billing model matters more than the headline rate:

| Provider | Billing unit | Best cadence | Your cost |
|---|---|---|---|
| **TwtAPI** | per **call** ($15/mo = 50k calls; 1 call ≈ 30-40 tweets) | **30 min** | **$15/mo** |
| **twitterapi.io** | per **tweet** ($0.15/1K) | **3-hourly** | $8.28/mo |
| **Sorsa** | per **request** (~20 tweets/req; $49/mo floor) | — | rarely worth it |

⚠️ "per 1K tweets" ads are misleading — count *calls per scan* for your exact
keyword set. 7 keywords/scan at 30-min cadence = 10,080 calls/mo (fits TwtAPI's
50k) but 331K tweets/mo ($49 on twitterapi.io). At 3-hourly the ranking flips.

## 🔌 Making it a Pond agent

The service speaks **Pond Protocol V1**: `GET /manifest` (public),
`POST /runs` (Bearer key + version header + idempotency). Start the service,
expose it at a public HTTPS URL, then publish it at
[joinpond.ai/agent/create](https://joinpond.ai/agent/create) — the manifest is schema-validated by `scripts/validate_pond_manifest.py`.

---

## 🧰 Extending (future platforms)

Add a new `BaseSource` subclass in `app/sources/`, register it in
`app/sources/__init__.py`, and set the cadence kind. No core changes — that's
the whole point of the pluggable design. Realistic next steps: Reddit,
ProductHunt, tech news, Hacker News.

---

## 📁 Project layout

```
app/
  main.py            FastAPI + scheduler + Pond endpoints
  config.py          pydantic-settings (env/.env)
  models.py          CompanySignal, Alert, founder helpers
  store.py           SQLite state (dedupe, pending, directory, kv)
  detect.py          EARLY / CONFIRMED / SPEEDRUN classifier
  loop.py            orchestrates one scan
  slack_notifier.py  Block Kit formatting + delivery
  sources/           yc_directory, speedrun, x, linkedin, base
scripts/             run_once, run_yc, render_demo, inspect helpers
tests/               pytest suite (24 passing)
docs/                demo alert screenshots
```

## ✅ Status

* 24/24 unit tests passing.
* Live-verified: Speedrun API (258 companies), YC directory (160 companies via
  headless Chrome), Pond `/manifest` + `/runs` auth, and rendered demo alerts.

## ⚖️ Legal & data hygiene

Public data only. yc-radar never logs into any platform, never automates an
account, never reuses cookies, and never republishes content — every alert
links back to the original post so founders keep the traffic and the credit.
LinkedIn removed a competitor (Proxycurl) from existence in court; our LinkedIn
path reads only public, already-indexed material through a commercial scraper
API with its own compliance obligations. If you operate in the EU, treat post
authors as data subjects under GDPR.

## 🖥️ Hosting honesty (2026)

Render free services spin down after 15 minutes idle — a scheduler dies with
them. Fly.io has no permanent free tier; Railway credit is one-time. For a
genuinely always-on monitor: any $5–7/month VPS, Oracle Cloud's free ARM VM,
or `systemd` on a box you already own (`scripts/yc-radar.service` is included).
The bot itself costs nothing to run beyond its optional data-provider keys.

---

# 🛰️ SONAR

**SONAR** is this project's product name — *Hear the launch before the world does.*

The metaphor: a sonar operator hears a submarine long before seeing it. A GTM
operator using SONAR hears a startup's launch signal (the founder's own
announcement) long before the directory shows it.

Vocabulary used across the UI and code: **ping** (one monitoring pass),
**return** (a matched signal), **echo-confirm** (the directory later confirms
an early call and the bot replies in-thread), **fuel** (data-provider credit
state, auto-managed), **scope** (the health dashboard).

The landing page is **part of this same project** — `app/static/index.html`
served at `/` by the app itself, and deployed separately to Vercel as a
static site (project `sonar-yc`). One codebase, both surfaces; no separate
landing-page repo to keep in sync.
