# -*- coding: utf-8 -*-
"""Publish the static site to GitHub Pages via the legacy gh-pages branch.

Why legacy instead of actions/upload-pages-artifact + deploy-pages:
the artifact upload goes through the Azure-backed artifact service, and on
this machine the runner's .NET/schannel TLS to that plane fails (SSL reset),
so the upload step dies before it can deploy. The legacy path needs nothing
but api.github.com: write the site to the gh-pages branch and GitHub's own
build service publishes it.

Everything here runs over api.github.com with OpenSSL.
"""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_api as ga  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, ".."))
REPO = "alexzhou41/cn-asset-lab"
SITE_DIR = os.path.join(WS, "site_cn_allweather")
PAGES_BRANCH = "gh-pages"


def resolve_token():
    t = os.environ.get("GITHUB_API_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    gh = r"C:\Program Files\GitHub CLI\gh.exe"
    if os.path.exists(gh):
        import subprocess
        r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise RuntimeError("no GitHub token available")


def site_files():
    """Every file under the built site, plus a .nojekyll marker."""
    out = {}
    for name in sorted(os.listdir(SITE_DIR)):
        path = os.path.join(SITE_DIR, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                out[name] = f.read()
    if ".nojekyll" not in out:
        out[".nojekyll"] = b""
    return out


def ensure_pages_config(tok):
    """Point Pages at the gh-pages branch (legacy build)."""
    try:
        cur = ga.gh(REPO, "GET", "/pages", tok=tok)
    except Exception as exc:  # noqa: BLE001
        print(f"  cannot read pages config: {exc}", flush=True)
        return False
    if cur.get("build_type") == "legacy" and \
            (cur.get("source") or {}).get("branch") == PAGES_BRANCH:
        print("  pages config already correct", flush=True)
        return True
    payload = {"build_type": "legacy", "source": {"branch": PAGES_BRANCH, "path": "/"}}
    try:
        ga.gh(REPO, "PUT", "/pages", payload, tok=tok)
        print("  pages config -> legacy / gh-pages", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  PUT /pages failed: {exc}", flush=True)
    return False


def publish(tok):
    files = site_files()
    print(f"site files: {[(k, len(v)) for k, v in files.items()]}", flush=True)

    try:
        base = ga.head_sha(REPO, PAGES_BRANCH, tok)
        print(f"gh-pages exists at {base[:8]}", flush=True)
    except Exception:
        base = ga.head_sha(REPO, "main", tok)
        print(f"creating gh-pages from main {base[:8]}", flush=True)
        ga.create_ref(REPO, PAGES_BRANCH, base, tok=tok)

    # the branch must not carry the main branch's content
    entries = [{"path": k, "sha": ga.create_blob(REPO, v, tok)} for k, v in files.items()]
    tree = ga.gh(REPO, "POST", "/git/trees",
                 {"tree": [{"path": e["path"], "mode": "100644", "type": "blob",
                            "sha": e["sha"]} for e in entries]}, tok=tok)["sha"]
    parent = ga.head_sha(REPO, PAGES_BRANCH, tok)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    commit = ga.create_commit(REPO, f"deploy: static site {stamp}", tree, [parent], tok=tok)
    ga.update_ref(REPO, PAGES_BRANCH, commit, tok=tok)
    print(f"gh-pages commit {commit}", flush=True)
    return commit


def main():
    tok = resolve_token()
    if not os.path.isdir(SITE_DIR):
        print("site directory missing:", SITE_DIR, flush=True)
        return 1
    ensure_pages_config(tok)
    publish(tok)
    print("PAGES_PUBLISHED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
