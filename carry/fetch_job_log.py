# -*- coding: utf-8 -*-
"""Download a GitHub Actions job log (zip) and print the interesting lines."""
import io
import os
import re
import sys
import zipfile

import requests

API = "https://api.github.com"


def main():
    repo = sys.argv[1]
    job = sys.argv[2]
    pattern = sys.argv[3] if len(sys.argv) > 3 else None
    tok = os.environ.get("GITHUB_API_TOKEN")
    s = requests.Session()
    s.trust_env = False
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    r = s.get(f"{API}/repos/{repo}/actions/jobs/{job}/logs", headers=headers,
              timeout=120, allow_redirects=True)
    print("http", r.status_code, "bytes", len(r.content), r.headers.get("content-type"))
    if r.status_code >= 400:
        print(r.text[:600])
        return 1
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
    except zipfile.BadZipFile:
        names = None
    rx = re.compile(pattern, re.I) if pattern else None
    if names:
        for n in names:
            text = z.read(n).decode("utf-8", "replace")
            for line in text.splitlines():
                if rx is None or rx.search(line):
                    print(f"[{n}] {line}")
    else:
        for line in r.text.splitlines():
            if rx is None or rx.search(line):
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
