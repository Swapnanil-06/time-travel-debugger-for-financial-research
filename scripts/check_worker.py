"""Start real workerd, run its HTTP contract tests, and stop the owned process."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    env = {**os.environ, "WRANGLER_SEND_METRICS": "false", "REPLAY_WORKER_URL": "http://127.0.0.1:8791"}
    with (artifacts / "workerd-check.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            "node", str(ROOT / "node_modules/wrangler/bin/wrangler.js"),
            "dev", "--port", "8791", "--inspector-port", "9237", "--log-level", "error",
        ], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            print("Starting actual Cloudflare Python runtime and SQLite-backed Durable Objects...", flush=True)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("workerd exited before becoming ready; see artifacts/workerd-check.log")
                try:
                    with urllib.request.urlopen(env["REPLAY_WORKER_URL"] + "/api/health", timeout=2) as result:
                        if result.status == 200:
                            break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError("workerd did not become ready; see artifacts/workerd-check.log")
            subprocess.run([sys.executable, "-m", "unittest", "tests.test_worker_live", "-v"], cwd=ROOT, env=env, check=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
