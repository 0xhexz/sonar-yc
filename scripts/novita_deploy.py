"""Provision a Novita Sandbox with SONAR and verify the endpoints respond.

Creates a sandbox, installs SONAR's deps, uploads the code, starts uvicorn
inside it, and returns the sandbox's public host info. Run with NOVITA_API_KEY
in the environment (read from .env if present).
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env
env_file = Path(__file__).resolve().parents[1] / ".env"
for line in env_file.read_text().splitlines():
    if line.startswith("NOVITA_API_KEY=") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)


async def main() -> None:
    from novita_sandbox import Novita

    novita = Novita()
    print("creating sandbox...")
    sandbox = novita.code_interpreter.create()
    print("sandbox id:", sandbox.sandbox_id)

    try:
        ex = sandbox.run_code("print('hello from SONAR sandbox')")
        print("exec logs:", ex.logs)

        # Install SONAR deps inside the sandbox
        print("installing deps (this takes a minute)...")
        deps = sandbox.run_code(
            "!pip install -q fastapi uvicorn httpx pydantic pydantic-settings "
            "apscheduler python-dotenv rapidfuzz PyYAML"
        )
        print("deps install done")

        # Upload the app code as a tarball
        import io
        import tarfile

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
        print(f"code bundle: {buf.getbuffer().nbytes} bytes")

        files = sandbox.files
        files.write("/home/user/sonar.tar.gz", buf.getvalue())
        sandbox.run_code(
            "import tarfile; tarfile.open('/home/user/sonar.tar.gz').extractall('/home/user/app')"
        )

        # Write the runtime env into the sandbox
        radar_env = (root / ".env").read_text()
        files.write("/home/user/app/.env", radar_env)

        # Start the server in the background
        sandbox.run_code(
            "import subprocess;"
            "subprocess.Popen(['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000'],"
            "cwd='/home/user/app')"
        )
        import time

        time.sleep(6)
        probe = sandbox.run_code(
            "import urllib.request;"
            "print(urllib.request.urlopen('http://127.0.0.1:8000/health').read()[:120])"
        )
        print("health probe:", probe.logs)

        # Expose via the sandbox's public host (Novita gives each sandbox a URL)
        host = sandbox.get_host(8000) if hasattr(sandbox, "get_host") else None
        print("public host (port 8000):", host)

        print("\nKEEP THIS RUNNING — do not kill the sandbox.")
        print("sandbox id (save):", sandbox.sandbox_id)
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc)
        raise


if __name__ == "__main__":
    asyncio.run(main())
