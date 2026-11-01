# -*- coding: utf-8 -*-
"""Local side of the cloud task: keep the workspace mirror current.

Division of labour
------------------
GitHub Actions (self-hosted runner on this machine) is the single data
producer: fetch -> merge snapshot -> recompute post-trade returns -> rebuild
site -> publish to GitHub -> deploy GitHub Pages.

This script is the local half:
  1. make sure the self-hosted runner is online (it is a plain background
     process, so it dies on reboot);
  2. pull the freshly published artifacts back into the workspace through
     api.github.com, so the desktop Mini App and the China-accessible platform
     mirror stay current;
  3. if the cloud schedule has not succeeded for a while (typically because the
     machine was asleep and the runner was offline), do one catch-up fetch
     locally and ask the cloud workflow to redeploy.

Everything talks to api.github.com over OpenSSL because github.com itself is
unreliable on this machine (schannel TLS gets reset).

Usage:
  python cloud_task_sync.py [--catchup-if-stale] [--stale-hours 8]
"""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_api as ga  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, ".."))
REPO = "alexzhou41/cn-asset-lab"
BRANCH = "main"
WORKFLOW = "update-site.yml"
RUNNER_ROOT = r"C:\Users\thinkpad\.minimax\actions-runner"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
DETACHED = 0x00000008 | 0x00000200
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy")

# repository path -> workspace path
ARTIFACTS = [
    ("carry/output/policy_trades_snapshot.csv",
     os.path.join(WS, "carry", "output", "policy_trades_snapshot.csv")),
    ("carry/output/policy_trades.csv",
     os.path.join(WS, "carry", "output", "policy_trades.csv")),
    ("carry/output/policy_analysis.json",
     os.path.join(WS, "carry", "output", "policy_analysis.json")),
    ("miniapps/china-all-weather/miniapp/client/data.json",
     os.path.join(WS, "miniapps", "china-all-weather", "miniapp", "client", "data.json")),
    ("site_cn_allweather/index.html",
     os.path.join(WS, "site_cn_allweather", "index.html")),
]


def clean_env():
    """Drop the local proxy vars: 127.0.0.1:7897 fails TLS for api.github.com."""
    env = dict(os.environ)
    for k in PROXY_VARS:
        env.pop(k, None)
    env["NO_PROXY"] = "github.com,api.github.com,*.github.com,*.githubusercontent.com"
    return env


def resolve_token():
    t = os.environ.get("GITHUB_API_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    if os.path.exists(GH):
        r = subprocess.run([GH, "auth", "token"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=clean_env())
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise RuntimeError("no GitHub token available")


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=clean_env())


# ------------------------------------------------------------------ runner

def runner_status(tok):
    data = ga.gh(REPO, "GET", "/actions/runners", tok=tok)
    for r in data.get("runners", []):
        if r.get("name") == "cnassetlab-win":
            return r.get("status", "unknown")
    return "missing"


def ensure_runner(tok):
    status = runner_status(tok)
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
    status = runner_status(tok)
    print(f"runner: {status}", flush=True)
    return status == "online"


# -------------------------------------------------------------------- sync

def pull(tok):
    tree, _ = ga.list_tree(REPO, BRANCH, tok)
    n = 0
    for repo_path, dest in ARTIFACTS:
        info = tree.get(repo_path)
        if not info:
            print(f"  not on remote: {repo_path}", flush=True)
            continue
        data = ga.read_blob(REPO, info["sha"], tok)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  synced {repo_path} ({len(data)} bytes)", flush=True)
        n += 1
    return n


def summarize():
    path = os.path.join(WS, "carry", "output", "policy_trades_snapshot.csv")
    if not os.path.exists(path):
        print("snapshot missing", flush=True)
        return None
    import pandas as pd
    df = pd.read_csv(path)
    d = pd.to_datetime(df["txDate"], errors="coerce")
    latest = d.max().date()
    print(f"snapshot rows={len(df)} latest_tx={latest} earliest_tx={d.min().date()}",
          flush=True)
    return len(df), latest


def recent_runs(tok):
    data = ga.gh(REPO, "GET", f"/actions/workflows/{WORKFLOW}/runs?per_page=10", tok=tok)
    return data.get("workflow_runs", [])


def last_success_age_hours(tok):
    runs = recent_runs(tok)
    if not runs:
        print("no workflow runs found", flush=True)
        return None
    now = dt.datetime.now(dt.timezone.utc)
    for r in runs[:5]:
        created = dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        age = (now - created).total_seconds() / 3600.0
        print(f"  run {r['id']} {r['status']}/{r['conclusion']} "
              f"{age:.1f}h ago", flush=True)
        if r["conclusion"] == "success":
            return age
    return None


def catchup(tok):
    """Local fetch + publish, then ask the cloud workflow to redeploy."""
    print("--- local catch-up fetch ---", flush=True)
    r = run([sys.executable, "-X", "utf8", "-u", os.path.join(HERE, "policy_refresh.py")])
    tail = [x for x in (r.stdout or "").splitlines() if x.strip()][-8:]
    for line in tail:
        print("   ", line, flush=True)
    if "REFRESH_OK" not in (r.stdout or ""):
        print("catch-up fetch did not complete; leaving the snapshot untouched", flush=True)
        return False
    r = run([sys.executable, "-X", "utf8", "-u", os.path.join(HERE, "api_commit.py"),
             "--skip", ".github/workflows/update-site.yml"])
    print("   ", (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "",
          flush=True)
    if r.returncode != 0:
        print("catch-up publish failed", flush=True)
        return False
    try:
        ga.gh(REPO, "POST", f"/actions/workflows/{WORKFLOW}/dispatches",
              {"ref": BRANCH, "inputs": {"deploy_only": "true"}}, tok=tok)
        print("   asked the cloud workflow to redeploy", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"   dispatch failed: {exc}", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catchup-if-stale", action="store_true",
                    help="fetch locally when the cloud schedule has not succeeded recently")
    ap.add_argument("--stale-hours", type=float, default=8.0)
    args = ap.parse_args()

    tok = resolve_token()
    ensure_runner(tok)
    age = last_success_age_hours(tok)
    if args.catchup_if_stale and (age is None or age > args.stale_hours):
        print(f"cloud schedule stale ({age}) -> local catch-up", flush=True)
        catchup(tok)
    pull(tok)
    summarize()
    print("SYNC_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
