# -*- coding: utf-8 -*-
"""Publish the workspace state to GitHub over api.github.com.

Why not `git push`: on this machine github.com is unreliable (Windows schannel
TLS gets reset; git works only after `http.sslBackend=openssl`), while
api.github.com is stable over OpenSSL. See github_api.py.

What gets published:
  * the refreshed congressional-trading artifacts (snapshot, trades, analysis)
  * the rebuilt mini app payload (data.json) and the rebuilt static site
  * the pipeline source files that produce them, so the repository stays a
    complete, runnable mirror of the workspace

Only files whose content actually changed are included in the commit.

Usage:
  python api_commit.py [--skip <repo/path>]...
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_api as ga  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, ".."))
REPO = "alexzhou41/cn-asset-lab"
BRANCH = "main"

# Pipeline scripts that must live in the repository for it to be reproducible.
PIPELINE_SCRIPTS = [
    "policy_refresh.py",      # incremental fetch -> merge -> recompute -> rebuild
    "policy_analysis.py",     # snapshot merge + post-trade returns
    "cn_policy_export.py",    # writes mini app data.json payload
    "cn_site_build.py",       # rebuilds the static site
    "us_prices.py",           # Sina US daily closes with local cache
    "collect_policy_trades.py",
    "cn_strategies.py",       # A-share allocation engine
    "github_api.py",          # api.github.com transport
    "api_commit.py",          # this publisher
    "cloud_task_sync.py",     # local mirror sync
    "setup_selfhosted_runner.py",
    "fetch_job_log.py",
    "pages_deploy.py",        # publishes the site to the gh-pages branch
]

# workspace path -> repository path
EXPLICIT = [
    (os.path.join(WS, "carry", "output", "policy_trades_snapshot.csv"),
     "carry/output/policy_trades_snapshot.csv"),
    (os.path.join(WS, "carry", "output", "policy_trades.csv"),
     "carry/output/policy_trades.csv"),
    (os.path.join(WS, "carry", "output", "policy_analysis.json"),
     "carry/output/policy_analysis.json"),
    (os.path.join(WS, "miniapps", "china-all-weather", "miniapp", "client", "data.json"),
     "miniapps/china-all-weather/miniapp/client/data.json"),
    (os.path.join(WS, "miniapps", "china-all-weather", "miniapp", "client", "index.html"),
     "miniapps/china-all-weather/miniapp/client/index.html"),
    (os.path.join(WS, "site_cn_allweather", "index.html"),
     "site_cn_allweather/index.html"),
    (os.path.join(WS, "carry", "requirements.txt"), "requirements.txt"),
    (os.path.join(WS, "github_pages", ".github", "workflows", "update-site.yml"),
     ".github/workflows/update-site.yml"),
    (os.path.join(WS, "github_pages", "repo", "WORKFLOW.yml"), "WORKFLOW.yml"),
    (os.path.join(WS, "github_pages", "repo", ".gitignore"), ".gitignore"),
    (os.path.join(WS, "github_pages", "README.md"), "README.md"),
]


def collect():
    """Return [(repo_path, local_path)] for everything that should be published."""
    items = list(EXPLICIT)
    carry_dir = os.path.join(WS, "carry")
    for name in PIPELINE_SCRIPTS:
        items.append((os.path.join(carry_dir, name), f"carry/{name}"))
    return items


def resolve_token():
    t = os.environ.get("GITHUB_API_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    gh = r"C:\Program Files\GitHub CLI\gh.exe"
    if os.path.exists(gh):
        r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise RuntimeError("no GitHub token available")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", action="append", default=[],
                    help="repository path to skip (repeatable)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--branch", default=BRANCH)
    args = ap.parse_args()
    skip = set(args.skip)

    tok = resolve_token()
    remote, _ = ga.list_tree(args.repo, args.branch, tok)
    print(f"remote blobs on {args.branch}: {len(remote)}", flush=True)

    changed, missing = [], []
    for local, repo_path in collect():
        if repo_path in skip:
            print(f"  skipped by request: {repo_path}", flush=True)
            continue
        if not os.path.exists(local):
            missing.append(repo_path)
            continue
        with open(local, "rb") as f:
            data = f.read()
        local_sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        cur = remote.get(repo_path)
        if cur and cur["sha"] == local_sha:
            continue
        changed.append((repo_path, data, cur["sha"] if cur else None))

    for repo_path in missing:
        print(f"  missing locally, skipped: {repo_path}", flush=True)
    if not changed:
        print("nothing changed; no commit", flush=True)
        print("PUBLISH_NOOP")
        return 0

    for repo_path, data, old in changed:
        print(f"  changed: {repo_path} ({len(data)} bytes, old={old})", flush=True)

    payload = {repo_path: data for repo_path, data, _old in changed}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res = ga.commit_files(args.repo, payload, f"chore: refresh site and data {stamp}",
                          ref=args.branch, tok=tok)
    print(f"commit {res['commit']} on {args.branch}", flush=True)
    print("PUBLISH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
