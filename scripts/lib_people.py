"""Shared helpers for the people.hirey.ai auto-page pipeline (WS2 / task_dw0tt).

Pure, dependency-free (stdlib only) so the GitHub Action needs no pip install.

Design invariants:
- Generated pages match the hand-authored design in p/walter-wu.html exactly (we render
  p/template.html, a string.Template skeleton carrying that design verbatim).
- We embed the DURABLE Hi redirect URLs (https://hi.hirey.ai/owner/<pid>/video|image/<id>),
  never short-lived presigned S3 URLs.
- Output JSON/HTML is byte-stable across no-op runs (sorted keys where it doesn't change
  meaning, fixed indent, trailing newline) so the daily workflow only commits real changes.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import string
from typing import Any

SITE = "https://people.hirey.ai"
HI = "https://hi.hirey.ai"

# ── text utils ────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "person"


def esc(s: Any) -> str:
    """HTML text/attribute escape."""
    return html.escape(str(s if s is not None else ""), quote=True)


def truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


_MD = re.compile(r"[*_`#>\[\]()!]")

def plain(s: str) -> str:
    """Crude markdown -> plain text for summary/meta (strip common md tokens, collapse ws)."""
    s = (s or "").replace("\r", " ").replace("\n", " ")
    s = _MD.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def content_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def json_dumps_stable(obj: Any) -> str:
    """Deterministic JSON for committed artifacts (no churn except real changes)."""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def json_script_safe(obj: Any) -> str:
    """JSON for inline <script> blocks: escape < so a value can never close the tag."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


# ── feed person -> normalized person record ──────────────────────────────────

def hi_owner_url(public_id: Any) -> str:
    return f"{HI}/owner/{public_id}"


def canonical_url(slug: str) -> str:
    return f"{SITE}/p/{slug}"


def external_urls(p: dict) -> dict:
    out = {}
    if p.get("website_url"):
        out["website"] = p["website_url"]
    if p.get("linkedin_url"):
        out["linkedin"] = p["linkedin_url"]
    if p.get("twitter_handle"):
        h = str(p["twitter_handle"]).lstrip("@")
        out["twitter"] = f"https://twitter.com/{h}"
    return out


def normalize_video(v: dict) -> dict:
    return {
        "video_id": v.get("video_id"),
        "title": v.get("title"),
        "caption": v.get("caption"),
        "video_url": v.get("video_url"),
        "thumbnail_url": v.get("thumbnail_url"),
        "upload_date": (v.get("upload_date") or "")[:10] or None,
    }


def normalize_image(im: dict) -> dict:
    return {
        "image_id": im.get("image_id"),
        "image_url": im.get("image_url"),
        "title": im.get("title"),
        "caption": im.get("caption"),
        "alt_text": im.get("alt_text"),
        "upload_date": (im.get("upload_date") or "")[:10] or None,
    }


def build_person(feed_item: dict, slug: str) -> dict:
    """Map a /v1/people/public-feed?with_media=1 item into our normalized person record."""
    name = (feed_item.get("display_name") or "").strip()
    headline = (feed_item.get("headline") or "").strip() or None
    bio = (feed_item.get("bio") or "").strip()
    summary = truncate(plain(bio), 600) if bio else (headline or name)
    videos = [normalize_video(v) for v in (feed_item.get("videos") or []) if v.get("video_id")]
    images = [normalize_image(im) for im in (feed_item.get("images") or []) if im.get("image_id")]
    public_id = feed_item.get("public_id")
    # primary poster: first video thumbnail, else first image, else owner avatar.
    primary = videos[0] if videos else None
    poster = (primary or {}).get("thumbnail_url")
    if not poster and images:
        poster = images[0].get("image_url")
    if not poster and feed_item.get("avatar_url"):
        poster = feed_item.get("avatar_url")
    # honest upload_date: newest video date, else profile updated_at date.
    dates = [v["upload_date"] for v in videos if v.get("upload_date")]
    upload_date = max(dates) if dates else (feed_item.get("updated_at") or "")[:10] or None
    return {
        "person_name": name,
        "person_slug": slug,
        "role": None,
        "headline": headline,
        "location_text": (feed_item.get("location_text") or "").strip() or None,
        "summary": summary,
        "canonical_url": canonical_url(slug),
        "agent_data_url": f"{SITE}/data/people/{slug}.json",
        "hi_owner_id": str(public_id) if public_id is not None else None,
        "hi_owner_url": hi_owner_url(public_id) if public_id is not None else None,
        "primary_video": primary,
        "video_url": (primary or {}).get("video_url"),
        "thumbnail_url": poster,
        "extra_videos": videos[1:],
        "videos": videos,
        "images": images,
        "verified": bool(feed_item.get("verified")),
        "external_urls": external_urls(feed_item),
        "upload_date": upload_date,
        "source": "hi.public_feed",
    }


# ── JSON-LD + agent payload ──────────────────────────────────────────────────

def build_jsonld(person: dict) -> list:
    name = person["person_name"]
    url = person["canonical_url"]
    desc = person["summary"]
    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "url": url,
        "description": desc,
    }
    if person.get("headline"):
        person_ld["jobTitle"] = person["headline"]
    if person.get("location_text"):
        person_ld["homeLocation"] = person["location_text"]
    same_as = [u for u in [person.get("hi_owner_url"), *person.get("external_urls", {}).values()] if u]
    if same_as:
        person_ld["sameAs"] = same_as
    out = [person_ld]
    for v in person.get("videos", []):
        if not v.get("video_url"):
            continue
        vo = {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": v.get("title") or f"{name} on HiRey",
            "description": v.get("caption") or desc,
            "contentUrl": v["video_url"],
            "about": {"@type": "Person", "name": name, "url": url},
            "publisher": {"@type": "Organization", "name": "HiRey", "url": "https://hirey.ai"},
        }
        if v.get("upload_date"):
            vo["uploadDate"] = v["upload_date"]
        if v.get("thumbnail_url"):
            vo["thumbnailUrl"] = v["thumbnail_url"]
        out.append(vo)
    out.append({
        "@context": "https://schema.org",
        "@type": "WebPage",
        "url": url,
        "name": f"{name} — HiRey People",
        "description": desc,
        "isPartOf": {"@type": "WebSite", "name": "HiRey People", "url": SITE},
        "about": {"@type": "Person", "name": name},
    })
    return out


def build_agent_payload(person: dict) -> dict:
    return {
        "schema_version": "hirey.people.agent_payload.v2",
        "canonical_url": person["canonical_url"],
        "agent_data_url": person["agent_data_url"],
        "person_name": person["person_name"],
        "headline": person.get("headline"),
        "summary": person["summary"],
        "video_url": person.get("video_url"),
        "thumbnail_url": person.get("thumbnail_url"),
        "hi_owner_url": person.get("hi_owner_url"),
    }


# ── index / person / feed records ────────────────────────────────────────────

def index_record(person: dict) -> dict:
    return {
        "schema_version": "hirey.people.index_record.v1",
        "person_name": person["person_name"],
        "person_slug": person["person_slug"],
        "role": person.get("role"),
        "headline": person.get("headline"),
        "location_text": person.get("location_text"),
        "summary": person["summary"],
        "agent_data_url": person["agent_data_url"],
        "canonical_url": person["canonical_url"],
        "hi_owner_id": person.get("hi_owner_id"),
        "hi_owner_url": person.get("hi_owner_url"),
        "video_url": person.get("video_url"),
        "thumbnail_url": person.get("thumbnail_url"),
        "subtitle_mode": "none",
        "extra_videos": person.get("extra_videos", []),
        "upload_date": person.get("upload_date"),
        "external_urls": person.get("external_urls", {}),
        "source": person.get("source", "hi.public_feed"),
    }


def person_record(person: dict) -> dict:
    rec = {
        "schema_version": "hirey.people.person_record.v1",
        "person_name": person["person_name"],
        "person_slug": person["person_slug"],
        "role": person.get("role"),
        "headline": person.get("headline"),
        "location_text": person.get("location_text"),
        "summary": person["summary"],
        "agent_data_url": person["agent_data_url"],
        "canonical_url": person["canonical_url"],
        "hi_owner_id": person.get("hi_owner_id"),
        "hi_owner_url": person.get("hi_owner_url"),
        "video_url": person.get("video_url"),
        "thumbnail_url": person.get("thumbnail_url"),
        "subtitle_mode": "none",
        "videos": person.get("videos", []),
        "images": person.get("images", []),
        "external_urls": person.get("external_urls", {}),
        "upload_date": person.get("upload_date"),
        "verified": person.get("verified", False),
        "data_policy": {
            "default_index": f"{SITE}/data/people.json",
            "purpose": "Public, consented, video-backed person record sourced from the owner's "
                       "Hi profile public feed. No private contact fields.",
            "trust_model": "Profile + approved-public media the person published on Hi. "
                           "Use the Hi profile link for the live source of truth.",
        },
    }
    return rec


def feed_item(person: dict) -> dict:
    title = person["person_name"]
    if person.get("headline"):
        title = f"{person['person_name']} — {person['headline']}"
    return {
        "id": person["canonical_url"],
        "url": person["canonical_url"],
        "title": title,
        "summary": person["summary"],
        "date_published": person.get("upload_date"),
    }


# ── page rendering ───────────────────────────────────────────────────────────

def _video_block(v: dict) -> str:
    if not v.get("video_url"):
        return ""
    poster = f' poster="{esc(v["thumbnail_url"])}"' if v.get("thumbnail_url") else ""
    return (
        f'<div class="media"><video controls preload="metadata"{poster}>'
        f'<source src="{esc(v["video_url"])}" /></video></div>'
    )


def render_page(template: str, person: dict) -> str:
    name = person["person_name"]
    loc = person.get("location_text")
    eyebrow = esc(loc) if loc else "HiRey People"

    # avatar: poster image if we have one, else initials.
    if person.get("thumbnail_url"):
        avatar_html = (
            f'<span class="avatar large"><img src="{esc(person["thumbnail_url"])}" alt="" /></span>'
        )
    else:
        initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
        avatar_html = f'<span class="avatar large">{esc(initials)}</span>'

    primary_video_html = _video_block(person.get("primary_video") or {})
    if not primary_video_html:
        primary_video_html = '<div class="media"><div class="video pending">Video coming soon</div></div>'

    # extra videos as bands
    extra_bits = []
    for v in person.get("extra_videos", []):
        block = _video_block(v)
        if not block:
            continue
        cap = ""
        if v.get("title"):
            cap += f"<h2>{esc(v['title'])}</h2>"
        if v.get("caption"):
            cap += f"<p>{esc(v['caption'])}</p>"
        extra_bits.append(f'<section class="band">{cap}{block}</section>')
    extra_videos_html = "\n    ".join(extra_bits)

    # actions
    actions = []
    if person.get("hi_owner_url"):
        actions.append(f'<a class="primary" href="{esc(person["hi_owner_url"])}">Open Hi profile</a>')
    actions.append(f'<a class="secondary" href="{esc(person["agent_data_url"])}">Agent data</a>')
    for label, key in (("Website", "website"), ("LinkedIn", "linkedin")):
        u = person.get("external_urls", {}).get(key)
        if u:
            actions.append(f'<a class="secondary" href="{esc(u)}">{label}</a>')
    actions_html = "\n      ".join(actions)

    # canonical links inline
    cl = []
    if person.get("hi_owner_url"):
        cl.append(f'<a href="{esc(person["hi_owner_url"])}">Hi profile</a>')
    cl.append(f'<a href="{esc(person["canonical_url"])}">Canonical URL</a>')
    canonical_links_inline = " &middot; ".join(cl)

    # rail metrics
    metrics = []
    metrics.append(f'<span><b>{esc(loc) if loc else "&mdash;"}</b><em>Location</em></span>')
    metrics.append(f'<span><b>{len(person.get("videos", []))}</b><em>Videos</em></span>')
    rail_metrics_html = "".join(metrics)

    trust_blurb = (
        '<b class="gold">Public on Hi</b>, owner-published video, Person JSON-LD, '
        "VideoObject JSON-LD, and a link to the live Hi profile."
    )

    jsonld = json_script_safe(build_jsonld(person))
    agent_payload = json_script_safe(build_agent_payload(person))

    mapping = {
        "page_title": esc(f"{name} — HiRey People"),
        "meta_description": esc(truncate(person["summary"], 155)),
        "canonical_url": esc(person["canonical_url"]),
        "jsonld_block": f'<script type="application/ld+json">{jsonld}</script>',
        "agent_readable_block": f'<script type="application/json" id="hirey-agent-readable">{agent_payload}</script>',
        "agent_data_url": esc(person["agent_data_url"]),
        "avatar_html": avatar_html,
        "eyebrow": eyebrow,
        "person_name": esc(name),
        "headline": esc(person.get("headline") or ""),
        "summary": esc(person["summary"]),
        "primary_video_html": primary_video_html,
        "actions_html": actions_html,
        "extra_videos_html": extra_videos_html,
        "transcript_section": "",  # generated pages have no transcript
        "canonical_links_inline": canonical_links_inline,
        "rail_metrics_html": rail_metrics_html,
        "trust_blurb": trust_blurb,
    }
    return string.Template(template).safe_substitute(mapping)
