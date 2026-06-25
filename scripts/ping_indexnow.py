#!/usr/bin/env python3
"""Ping IndexNow with the URLs that changed in this build.

Reads scripts/.cache/changed-urls.txt (written by build_people_pages.py). If empty, no-op.
Key: read from $INDEXNOW_KEY, else scripts/indexnow.key. The matching key file must be hosted
at https://people.hirey.ai/<key>.txt (committed at repo root) for IndexNow to verify ownership.

IndexNow is fan-out: Bing/Yandex/etc. share submissions. Google ignores IndexNow but picks the
pages up via the sitemap; this just accelerates the others. Failures are non-fatal (exit 0).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHANGED = os.path.join(HERE, ".cache", "changed-urls.txt")
KEYFILE = os.path.join(HERE, "indexnow.key")
HOST = "people.hirey.ai"
ENDPOINT = "https://api.indexnow.org/indexnow"


def load_key() -> str | None:
    k = (os.environ.get("INDEXNOW_KEY") or "").strip()
    if k:
        return k
    try:
        return open(KEYFILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def main() -> int:
    try:
        urls = [u.strip() for u in open(CHANGED, encoding="utf-8").read().splitlines() if u.strip()]
    except OSError:
        urls = []
    if not urls:
        sys.stderr.write("[indexnow] no changed URLs; skipping.\n")
        return 0
    key = load_key()
    if not key:
        sys.stderr.write("[indexnow] no key (INDEXNOW_KEY / scripts/indexnow.key); skipping.\n")
        return 0
    body = json.dumps({
        "host": HOST,
        "key": key,
        "keyLocation": f"https://{HOST}/{key}.txt",
        "urlList": urls[:10000],
    }).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            sys.stderr.write(f"[indexnow] submitted {len(urls)} URL(s); HTTP {resp.status}\n")
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[indexnow] HTTP {e.code} (non-fatal): {e.read()[:200]!r}\n")
    except (urllib.error.URLError, TimeoutError) as e:
        sys.stderr.write(f"[indexnow] network error (non-fatal): {e}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
