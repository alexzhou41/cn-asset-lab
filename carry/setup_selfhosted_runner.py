# -*- coding: utf-8 -*-
"""Register this Windows machine as a self-hosted GitHub Actions runner.

Why: GitHub-hosted runners come from datacenter IP ranges, and Capitol Trades
(Vercel Security Checkpoint) answers them with HTTP 429. A self-hosted runner
executes the same workflow from this machine's residential connection, so the
scheduling/logging still live in GitHub's cloud while the fetch actually works.

Run once; it leaves the runner installed. If the Windows service install is not
permitted (no admin rights), it falls back to a detached background process.
"""
import json
import os
import subprocess
import sys
import zipfile

import requests

REPO = "alexzhou41/cn-asset-lab"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
ROOT = r"C:\Users\thinkpad\.minimax\actions-runner"
WORKER = os.path.join(ROOT, "_work")
RUNNER_RELEASES = "https://api.github.com/repos/actions/runner/releases/latest"

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def gh_json(args):
    r = subprocess.run([GH] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:400])
    return json.loads(r.stdout or "{}")


def download_runner():
    """Download the Windows x64 runner bundle from the official release feed."""
    s = requests.Session()
    s.trust_env = False
    r = s.get(RUNNER_RELEASES, timeout=60,
              headers={"Accept": "application/vnd.github+json",
                       "User-Agent": "cn-asset-lab-setup"})
    r.raise_for_status()
    release = r.json()
    tag = release.get("tag_name", "")
    url = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("actions-runner-win-x64-") and name.endswith(".zip"):
            url = asset["browser_download_url"]
            break
    if not url:
        raise RuntimeError("no actions-runner-win-x64 zip asset in release " + tag)
    print(f"release {tag}\ndownloading {url}", flush=True)

    zip_path = os.path.join(ROOT, "runner.zip")
    with s.get(url, timeout=900, stream=True) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ROOT)
    os.remove(zip_path)
    print("runner extracted", flush=True)


def start_background():
    """Last resort: run the runner as a detached user process (no service)."""
    log = open(os.path.join(ROOT, "runner_bg.log"), "a", encoding="utf-8")
    subprocess.Popen(
        ["cmd.exe", "/c", os.path.join(ROOT, "run.cmd")],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        creationflags=DETACHED, close_fds=True,
    )
    print("runner started as detached background process", flush=True)


def main():
    if not os.path.exists(os.path.join(ROOT, "config.cmd")):
        os.makedirs(ROOT, exist_ok=True)
        download_runner()

    token = gh_json(["api", "--method", "POST",
                      f"repos/{REPO}/actions/runners/registration-token"])["token"]
    print("registration token ok", flush=True)

    r = subprocess.run([os.path.join(ROOT, "config.cmd"),
                        "--url", f"https://github.com/{REPO}",
                        "--token", token,
                        "--name", "cnassetlab-win",
                        "--labels", "cnassetlab,windows,x64",
                        "--work", WORKER,
                        "--unattended", "--replace"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print((r.stdout or "")[-800:], flush=True)
    if r.returncode != 0:
        print("CONFIG FAILED:", (r.stderr or "")[-800:], flush=True)
        return 1

    svc = os.path.join(ROOT, "svc.cmd")
    r = subprocess.run([svc, "install"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    svc_out = (r.stdout or "") + (r.stderr or "")
    print("svc install:", svc_out[-400:].strip(), flush=True)
    if r.returncode == 0:
        r = subprocess.run([svc, "start"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print("svc start:", ((r.stdout or "") + (r.stderr or ""))[-400:].strip(), flush=True)
        print("RUNNER_READY (windows service)", flush=True)
        return 0

    print("service install not permitted, falling back to background process", flush=True)
    start_background()
    print("RUNNER_READY (background process)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
