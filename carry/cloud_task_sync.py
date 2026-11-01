# -*- coding: utf-8 -*-
"""Keep the local mirror in sync with the cloud task.

Division of labour
------------------
GitHub Actions (self-hosted runner running on this machine) is the single data
producer: fetch -> merge snapshot -> recompute post-trade returns -> rebuild
site -> commit -> deploy GitHub Pages.

This script is the local side of that pipeline:
  1. make sure the self-hosted runner is online (it is a plain background
     process, so it dies on reboot);
  2. pull the freshly committed artifacts back into the workspace, so the
     desktop Mini App and the China-accessible platform mirror stay current.

It never fetches from Capitol Trades itself, so the two halves can never
double-hit the source or diverge.
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.join(WS, "github_pages", "repo")
RUNNER_ROOT = r"C:\Users\thinkpad\.minimax\actions-runner"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
GH_REPO = "alexzhou41/cn-asset-lab"

ARTIFACTS = [
    (os.path.join("carry", "output", "policy_trades_snapshot.csv"),
     os.path.join(WS, "carry", "output", "policy_trades_snapshot.csv")),
    (os.path.join("carry", "output", "policy_trades.csv"),
     os.path.join(WS, "carry", "output", "policy_trades.csv")),
    (os.path.join("carry", "output", "policy_analysis.json"),
     os.path.join(WS, "carry", "output", "policy_analysis.json")),
    (os.path.join("miniapps", "china-all-weather", "miniapp", "client", "data.json"),
     os.path.join(WS, "miniapps", "china-all-weather", "miniapp", "client", "data.json")),
    (os.path.join("site_cn_allweather", "index.html"),
     os.path.join(WS, "site_cn_allweather", "index.html")),
]

DETACHED = 0x00000008 | 0x00000200
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy")


def run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def clean_env():
    """Drop the local proxy vars: 127.0.0.1:7897 answers api.github.com with EOF."""
    env = dict(os.environ)
    for k in PROXY_VARS:
        env.pop(k, None)
    env["NO_PROXY"] = "github.com,api.github.com,*.github.com,*.githubusercontent.com"
    return env


def runner_status():
    r = run([GH, "api", f"repos/{GH_REPO}/actions/runners",
             "--jq", ".runners[]|select(.name==\"cnassetlab-win\")|.status"],
            env=clean_env())
    lines = (r.stdout or "").strip().splitlines()
    return lines[-1].strip() if lines else "unknown"


def ensure_runner():
    """Start the self-hosted runner if it is not currently online."""
    status = runner_status()
    if status == "online":
        print("runner: online", flush=True)
        return True
    print(f"runner: {status} -> starting background process", flush=True)
    if not os.path.exists(os.path.join(RUNNER_ROOT, "run.cmd")):
        print("runner not installed; skipping", flush=True)
        return False
    log = open(os.path.join(RUNNER_ROOT, "runner_bg.log"), "a", encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", os.path.join(RUNNER_ROOT, "run.cmd")],
                     cwd=RUNNER_ROOT, stdout=log, stderr=subprocess.STDOUT,
                     env=clean_env(), creationflags=DETACHED, close_fds=True)
    time.sleep(12)
    status = runner_status()
    print(f"runner: {status}", flush=True)
    return status == "online"


def pull():
    r = run(["git", "pull", "--ff-only", "origin", "main"], cwd=REPO, env=clean_env())
    out = (r.stdout or "") + (r.stderr or "")
    tail = [x.strip() for x in out.splitlines() if x.strip()][-3:]
    print("git pull:", " | ".join(tail), flush=True)
    return r.returncode == 0


def copy_artifacts():
    copied = []
    for rel, dest in ARTIFACTS:
        src = os.path.join(REPO, rel)
        if not os.path.exists(src):
            print(f"missing in repo: {rel}", flush=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        copied.append((rel, os.path.getsize(dest)))
    for rel, size in copied:
        print(f"synced {rel} ({size} bytes)", flush=True)
    return copied


def summarize():
    path = os.path.join(WS, "carry", "output", "policy_trades_snapshot.csv")
    if not os.path.exists(path):
        return
    import pandas as pd
    df = pd.read_csv(path)
    d = pd.to_datetime(df["txDate"], errors="coerce")
    print(f"snapshot rows={len(df)} latest_tx={d.max().date()} "
          f"earliest_tx={d.min().date()}", flush=True)


def main():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print("repo copy missing:", REPO, flush=True)
        return 1
    ensure_runner()
    pull()
    copy_artifacts()
    summarize()
    print("SYNC_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
