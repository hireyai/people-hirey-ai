#!/usr/bin/env python3
"""Build people.hirey.ai person pages + aggregates from the cached Hi feed.

Reads scripts/.cache/people-feed-raw.json (from fetch_people_feed.py) and renders, for every
consented media-backed person who is NOT hand-curated:
  - p/<slug>/index.html        (from p/template.html — same design as the curated pages)
  - data/people/<slug>.json    (full person record)
then rebuilds the aggregates, MERGING the hand-curated people verbatim:
  - data/people.json   (all-people index)
  - feed.json          (JSON Feed v1.1)
  - sitemap.xml        (honest, hash-gated per-URL lastmod)

Guarantees:
  - NEVER touches the curated slugs in data/curated-overrides.json (Walter/Zubair/Curtis carry
    editorial content no endpoint produces).
  - Slugs are pinned per Hi owner in data/slug-map.json so a rename never breaks a canonical URL.
  - Generated people who leave the feed (e.g. went private) are pruned (page + record removed).
  - Every artifact is written only when its content actually changes; lastmod bumps only on real
    content change — so a no-op run produces an empty git diff (no daily-commit noise).
  - Writes scripts/.cache/changed-urls.txt = URLs whose content changed (for the IndexNow ping).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_people as L  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, ".cache", "people-feed-raw.json")
CHANGED_URLS = os.path.join(HERE, ".cache", "changed-urls.txt")
TEMPLATE = os.path.join(ROOT, "p", "template.html")
BUILD_DATE = os.environ.get("BUILD_DATE") or datetime.date.today().isoformat()

P_DATA_PEOPLE = os.path.join(ROOT, "data", "people")
P_PEOPLE_JSON = os.path.join(ROOT, "data", "people.json")
P_FEED = os.path.join(ROOT, "feed.json")
P_SITEMAP = os.path.join(ROOT, "sitemap.xml")
P_CURATED = os.path.join(ROOT, "data", "curated-overrides.json")
P_SLUGMAP = os.path.join(ROOT, "data", "slug-map.json")
P_GENSLUGS = os.path.join(ROOT, "data", "generated-slugs.json")
P_LASTMOD = os.path.join(ROOT, "data", "lastmod-state.json")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_if_changed(path: str, content: str) -> bool:
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == content:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def url_to_localpath(url: str) -> str | None:
    if not url.startswith(L.SITE):
        return None
    rel = url[len(L.SITE):].lstrip("/")
    if rel == "" or rel == "/":
        return os.path.join(ROOT, "index.html")
    if rel.startswith("p/") and "." not in rel.split("/")[-1]:
        return os.path.join(ROOT, rel, "index.html")
    return os.path.join(ROOT, rel)


def file_hash(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return L.content_hash(f.read())


def bootstrap_lastmod_state() -> dict:
    """Seed lastmod-state from the existing sitemap so curated URLs keep their current lastmod
    (their content hash matches now, so they won't be bumped on the first generated run)."""
    state: dict = {}
    if not os.path.exists(P_SITEMAP):
        return state
    import re
    txt = open(P_SITEMAP, encoding="utf-8").read()
    for m in re.finditer(r"<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", txt):
        url, lastmod = m.group(1), m.group(2)
        state[url] = {"hash": file_hash(url_to_localpath(url)), "lastmod": lastmod}
    return state


def main() -> int:
    template = open(TEMPLATE, encoding="utf-8").read()
    feed = read_json(CACHE, {"people": []})
    feed_people = feed.get("people") or []
    curated_slugs = set((read_json(P_CURATED, {}).get("slugs")) or [])
    current_index = (read_json(P_PEOPLE_JSON, {}).get("people")) or []
    slug_map = read_json(P_SLUGMAP, {})
    prev_generated = set(read_json(P_GENSLUGS, []) or [])
    lastmod_state = read_json(P_LASTMOD, None)
    if lastmod_state is None:
        lastmod_state = bootstrap_lastmod_state()

    curated_index = [e for e in current_index if e.get("person_slug") in curated_slugs]
    used_slugs = set(curated_slugs) | set(slug_map.values())

    generated_people = []
    new_generated_slugs: set[str] = set()
    for item in feed_people:
        name = (item.get("display_name") or "").strip()
        pid = item.get("public_id")
        if not name or pid is None:
            continue
        owner_id = str(pid)
        slug = slug_map.get(owner_id)
        if not slug:
            base = L.slugify(name)
            slug = base if base not in used_slugs else f"{base}-{owner_id}"
            slug_map[owner_id] = slug
        used_slugs.add(slug)
        if slug in curated_slugs:
            continue  # never overwrite a hand-curated person
        generated_people.append(L.build_person(item, slug))
        new_generated_slugs.add(slug)

    # write generated pages + records. We write BOTH /p/<slug>/index.html AND the flat
    # /p/<slug>.html (same bytes) — matching the curated convention so the canonical URL
    # https://people.hirey.ai/p/<slug> resolves 200 directly (the flat file) instead of
    # 301-redirecting to the trailing-slash directory form.
    for person in generated_people:
        slug = person["person_slug"]
        page_html = L.render_page(template, person)
        write_if_changed(os.path.join(ROOT, "p", slug, "index.html"), page_html)
        write_if_changed(os.path.join(ROOT, "p", f"{slug}.html"), page_html)
        write_if_changed(os.path.join(P_DATA_PEOPLE, f"{slug}.json"), L.json_dumps_stable(L.person_record(person)))

    # prune generated people who left the feed (privacy: went private / no media)
    for slug in sorted(prev_generated - new_generated_slugs):
        if slug in curated_slugs:
            continue
        d = os.path.join(ROOT, "p", slug)
        if os.path.isdir(d):
            shutil.rmtree(d)
        for stray in (os.path.join(ROOT, "p", f"{slug}.html"), os.path.join(P_DATA_PEOPLE, f"{slug}.json")):
            if os.path.exists(stray):
                os.remove(stray)

    # aggregates: curated (verbatim) + generated
    generated_index = [L.index_record(p) for p in generated_people]
    all_index = curated_index + generated_index
    all_index.sort(key=lambda e: ((e.get("upload_date") or ""), e.get("person_slug") or ""), reverse=True)

    # people.json — keep prior generated_at when the people list is unchanged (no churn).
    old_people_doc = read_json(P_PEOPLE_JSON, {})
    gen_at = old_people_doc.get("generated_at") if old_people_doc.get("people") == all_index else BUILD_DATE
    people_doc = {
        "generated_at": gen_at,
        "policy": "Public, consented, video-backed people records only. No private contact fields.",
        "people": all_index,
    }
    write_if_changed(P_PEOPLE_JSON, L.json_dumps_stable(people_doc))

    # feed.json (JSON Feed v1.1) — newest first by date_published
    feed_items = sorted(
        (L.feed_item({**e, "person_name": e["person_name"]}) for e in all_index),
        key=lambda it: (it.get("date_published") or ""),
        reverse=True,
    )
    feed_doc = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "HiRey People",
        "home_page_url": L.SITE,
        "feed_url": f"{L.SITE}/feed.json",
        "items": feed_items,
    }
    write_if_changed(P_FEED, L.json_dumps_stable(feed_doc))

    # persist slug-map + generated-slugs manifest
    write_if_changed(P_SLUGMAP, L.json_dumps_stable(dict(sorted(slug_map.items()))))
    write_if_changed(P_GENSLUGS, L.json_dumps_stable(sorted(new_generated_slugs)))

    # sitemap with hash-gated lastmod
    urls: list[str] = [f"{L.SITE}/"]
    transcript_dir = os.path.join(ROOT, "data", "transcripts")
    for e in all_index:
        slug = e.get("person_slug")
        if not slug:
            continue
        urls.append(f"{L.SITE}/p/{slug}")
        urls.append(f"{L.SITE}/data/people/{slug}.json")
        if os.path.exists(os.path.join(transcript_dir, f"{slug}.txt")):
            urls.append(f"{L.SITE}/data/transcripts/{slug}.txt")
    urls += [f"{L.SITE}/data/people.json", f"{L.SITE}/feed.json", f"{L.SITE}/llms.txt"]

    changed_urls: list[str] = []
    new_state: dict = {}
    for url in urls:
        h = file_hash(url_to_localpath(url))
        prev = lastmod_state.get(url)
        if prev and prev.get("hash") == h:
            lastmod = prev.get("lastmod") or BUILD_DATE
        else:
            lastmod = BUILD_DATE
            changed_urls.append(url)
        new_state[url] = {"hash": h, "lastmod": lastmod}

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        sitemap.append(f"<url><loc>{url}</loc><lastmod>{new_state[url]['lastmod']}</lastmod></url>")
    sitemap.append("</urlset>\n")
    write_if_changed(P_SITEMAP, "\n".join(sitemap))
    write_if_changed(P_LASTMOD, L.json_dumps_stable(new_state))

    os.makedirs(os.path.dirname(CHANGED_URLS), exist_ok=True)
    with open(CHANGED_URLS, "w", encoding="utf-8") as f:
        f.write("\n".join(changed_urls))

    sys.stderr.write(
        f"[build] curated={len(curated_index)} generated={len(generated_people)} "
        f"pruned={len(prev_generated - new_generated_slugs)} changed_urls={len(changed_urls)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
