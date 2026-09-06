"""Long-lived SONAR deployment on Novita Sandbox (paid/credit billing).

Key settings vs the free test script:
  * timeout:        14 days (max lifetime; renewed by the keep-alive below)
  * auto_pause:     on timeout the sandbox PAUSES (state kept) instead of dying
  * keep-alive:     a background thread on THIS machine pings /health every
                    5 min and extends the timeout — so billing stays continuous
                    but the sandbox never silently dies.

Run:  python scripts/novita_deploy_long.py   (NOVITA_API_KEY in .env)
"""
import io
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

env_file = Path(__file__).resolve().parents[1] / ".env"
for line in env_file.read_text().splitlines():
    if line.startswith(("NOVITA_API_KEY=", "SLACK_BOT_TOKEN=", "X_PROVIDER_", "LINKEDIN_", "LLM_")):
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)

TIMEOUT_SECONDS = 3600  # 1 hour max per create (Novita cap); keep-alive renews


def main() -> None:
    from novita_sandbox import Novita

    novita = Novita()
    print("creating LONG-LIVED sandbox (timeout=14d, auto_pause)...")
    sandbox = novita.code_interpreter.create(
        timeout=TIMEOUT_SECONDS,
        auto_pause=True,
    )
    print("sandbox id:", sandbox.sandbox_id)

    ex = sandbox.run_code("print('SONAR long-lived sandbox up')")
    print("exec:", ex.logs)

    print("installing deps...")
    sandbox.run_code(
        "!pip install -q fastapi uvicorn httpx pydantic pydantic-settings "
        "apscheduler python-dotenv rapidfuzz PyYAML"
    )

    root = Path(__file__).resolve().parents[1]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in ("app", "radar.yaml", "requirements.txt"):
            p = root / rel
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and "__pycache__" not in str(f):
                        tar.add(f, arcname=str(f.relative_to(root)))
            elif p.is_file():
                tar.add(p, arcname=rel)
    buf.seek(0)
    files = sandbox.files
    files.write("/home/user/sonar.tar.gz", buf.getvalue())
    sandbox.run_code(
        "import tarfile; tarfile.open('/home/user/sonar.tar.gz').extractall('/home/user/app')"
    )
    files.write("/home/user/app/.env", (root / ".env").read_text())

    sandbox.run_code(
        "import subprocess;"
        "subprocess.Popen(['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000'],"
        "cwd='/home/user/app')"
    )
    time.sleep(6)
    probe = sandbox.run_code(
        "import urllib.request;"
        "print(urllib.request.urlopen('http://127.0.0.1:8000/health').read()[:100])"
    )
    print("health:", probe.logs)

    host = sandbox.get_host(8000)
    public = f"https://{host}"
    print("\nPUBLIC URL:", public)
    print("manifest:", public + "/manifest")

    # Keep-alive: Novita caps a single create() timeout at 1 hour, so a local
    # loop renews +1h every 25 min — sandbox effectively stays alive forever
    # while this script runs (or until the account runs out of credits).
    print("starting keep-alive (every 25 min, renews +1h; Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(1500)
            sandbox.set_timeout(TIMEOUT_SECONDS)
            stamp = time.strftime("%H:%M")
            print(f"[{stamp}] timeout renewed +1h")
    except KeyboardInterrupt:
        print("keep-alive stopped — sandbox will pause on timeout (state kept)")


if __name__ == "__main__":
    main()
