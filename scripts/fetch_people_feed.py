#!/usr/bin/env python3
"""Fetch the consented, media-backed people feed from Hi into a local cache.

Source: GET https://hi.hirey.ai/v1/people/public-feed?has_media=1&with_media=1
  - has_media=1  : MANDATORY privacy/quality gate — without it the default-public feed
                   returns thousands of bare imported leads. Only people with at least one
                   approved-public image/video/article are returned.
  - with_media=1 : embeds per-person bio + videos[]/images[] (durable /owner/.. URLs).

Writes scripts/.cache/people-feed-raw.json (git-ignored — not a committed artifact).
Keep-old-on-failure: if the endpoint is unreachable, the previous cache is left intact
(we never blow away good data because of a transient outage).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("HI_FEED_BASE", "https://hi.hirey.ai")
ENDPOINT = f"{BASE}/v1/people/public-feed"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE = os.path.join(CACHE_DIR, "people-feed-raw.json")
PAGE_LIMIT = 50
MAX_PAGES = 200  # safety backstop (10k people)


def fetch_page(cursor: str | None) -> dict:
    params = f"limit={PAGE_LIMIT}&has_media=1&with_media=1"
    if cursor:
        params += f"&cursor={urllib.parse.quote(cursor)}"
    url = f"{ENDPOINT}?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "people-hirey-ai-builder"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    people: list[dict] = []
    cursor: str | None = None
    pages = 0
    try:
        while pages < MAX_PAGES:
            page = fetch_page(cursor)
            batch = page.get("people") or []
            people.extend(batch)
            pages += 1
            cursor = page.get("next_cursor")
            if not cursor or not batch:
                break
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        sys.stderr.write(f"[fetch] FAILED ({e}); keeping existing cache untouched.\n")
        # keep-old-on-failure: do not overwrite a good cache with nothing.
        return 0 if os.path.exists(CACHE) else 1

    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {"source": ENDPOINT, "count": len(people), "people": people}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    sys.stderr.write(f"[fetch] {len(people)} people across {pages} page(s) -> {CACHE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
