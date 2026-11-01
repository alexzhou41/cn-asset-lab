# -*- coding: utf-8 -*-
"""Talk to GitHub purely through api.github.com (REST + Git Data API).

Why not plain git: on this machine github.com / codeload.github.com /
raw.githubusercontent.com are all unreachable (connect timeout), while
api.github.com responds normally over the direct connection. The local proxy
on 127.0.0.1:7897 currently fails TLS for every host. So the only GitHub
transport available here is the REST API on api.github.com.

The Git Data API (blobs / trees / commits / refs) covers both directions:
  read  : tree listing + blob download
  write : create blob -> create tree -> create commit -> update ref

api.github.com drops connections without warning now and then (SSL EOF), so
every call is retried with backoff.

Every request uses trust_env=False so the broken local proxy is bypassed.
"""
import base64
import os
import time

import requests

API = "https://api.github.com"
SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "cn-asset-lab",
})
RETRY_DELAYS = (2, 5, 12, 25)


def token(explicit=None):
    t = explicit or os.environ.get("GITHUB_API_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        raise RuntimeError("no token: set GITHUB_API_TOKEN / GH_TOKEN")
    return t.strip()


def gh(repo, method, path, payload=None, tok=None, timeout=180):
    url = f"{API}/repos/{repo}{path}"
    headers = {"Authorization": f"Bearer {token(tok)}"}
    last = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            r = SESSION.request(method, url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last = f"{type(exc).__name__}: {str(exc)[:160]}"
            if attempt < len(RETRY_DELAYS):
                wait = RETRY_DELAYS[attempt]
                print(f"  [retry {attempt + 1}] {method} {path} -> {last}; sleep {wait}s",
                      flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{method} {url} failed after retries: {last}")
        if r.status_code >= 500 and attempt < len(RETRY_DELAYS):
            wait = RETRY_DELAYS[attempt]
            print(f"  [retry {attempt + 1}] {method} {path} -> HTTP {r.status_code}; "
                  f"sleep {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content
    raise RuntimeError(f"{method} {url} failed: {last}")


# ---------------------------------------------------------------- read side

def list_tree(repo, ref="main", tok=None):
    """Return ({path: {sha, size}}, tree_sha) for the whole repo at ref."""
    data = gh(repo, "GET", f"/git/trees/{ref}?recursive=1", tok=tok)
    out = {}
    for node in data.get("tree", []):
        if node.get("type") == "blob":
            out[node["path"]] = {"sha": node["sha"], "size": node.get("size", 0)}
    return out, data.get("sha")


def read_blob(repo, sha, tok=None):
    data = gh(repo, "GET", f"/git/blobs/{sha}", tok=tok)
    return base64.b64decode(data["content"])


def file_sha(repo, path, ref="main", tok=None):
    return list_tree(repo, ref, tok)[0][path]["sha"]


# --------------------------------------------------------------- write side

def create_blob(repo, data: bytes, tok=None):
    payload = {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"}
    return gh(repo, "POST", "/git/blobs", payload, tok=tok)["sha"]


def create_tree(repo, base_tree, entries, tok=None):
    """entries: [{path, sha}] -> new tree sha (based on base_tree)."""
    payload = {
        "base_tree": base_tree,
        "tree": [{"path": e["path"], "mode": "100644", "type": "blob", "sha": e["sha"]}
                 for e in entries],
    }
    return gh(repo, "POST", "/git/trees", payload, tok=tok)["sha"]


def create_commit(repo, message, tree_sha, parents, tok=None):
    payload = {"message": message, "tree": tree_sha, "parents": list(parents)}
    return gh(repo, "POST", "/git/commits", payload, tok=tok)["sha"]


def update_ref(repo, ref, sha, force=False, tok=None):
    payload = {"sha": sha, "force": bool(force)}
    return gh(repo, "PATCH", f"/git/refs/heads/{ref}", payload, tok=tok)


def create_ref(repo, ref, sha, tok=None):
    payload = {"ref": f"refs/heads/{ref}", "sha": sha}
    return gh(repo, "POST", "/git/refs", payload, tok=tok)


def delete_ref(repo, ref, tok=None):
    return gh(repo, "DELETE", f"/git/refs/heads/{ref}", tok=tok)


def head_sha(repo, ref="main", tok=None):
    data = gh(repo, "GET", f"/git/ref/heads/{ref}", tok=tok)
    return data["object"]["sha"]


def commit_files(repo, files, message, ref="main", tok=None, dry_run=False):
    """Commit a dict {path: bytes} on top of ref. Returns the new commit sha."""
    base_commit = head_sha(repo, ref, tok)
    base_tree = list_tree(repo, base_commit, tok)[1]
    entries = []
    for path, data in files.items():
        if isinstance(data, str):
            data = data.encode("utf-8")
        entries.append({"path": path, "sha": create_blob(repo, data, tok)})
    new_tree = create_tree(repo, base_tree, entries, tok)
    new_commit = create_commit(repo, message, new_tree, [base_commit], tok)
    if dry_run:
        return {"base": base_commit, "tree": new_tree, "commit": new_commit, "entries": entries}
    update_ref(repo, ref, new_commit, tok=tok)
    return {"base": base_commit, "tree": new_tree, "commit": new_commit, "entries": entries}
