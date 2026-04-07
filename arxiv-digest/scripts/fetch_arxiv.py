#!/usr/bin/env python3
"""Fetch today's new papers from arxiv RSS feeds.

Usage:
    python fetch_arxiv.py                    # Fetch all configured categories
    python fetch_arxiv.py cs.AI cs.CV stat   # Fetch specific categories only

Output: JSON to stdout with papers grouped by category.
"""

import json
import re
import sys
import time
import urllib.request
from html import unescape
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Category configuration
# ---------------------------------------------------------------------------

TOP_LEVEL = ["cs", "math", "physics", "stat", "eess", "q-bio", "q-fin", "econ"]

# Autonomous-driving-related subcategories
CS_SUBS = ["cs.CV", "cs.RO", "cs.AI", "cs.LG", "cs.SY"]
STAT_SUBS = ["stat.ML", "stat.AP", "stat.ME"]

CATEGORY_LABELS = {
    "cs": "Computer Science",
    "math": "Mathematics",
    "physics": "Physics",
    "stat": "Statistics",
    "eess": "Electrical Engineering and Systems Science",
    "q-bio": "Quantitative Biology",
    "q-fin": "Quantitative Finance",
    "econ": "Economics",
    "cs.CV": "CS - Computer Vision and Pattern Recognition",
    "cs.RO": "CS - Robotics",
    "cs.AI": "CS - Artificial Intelligence",
    "cs.LG": "CS - Machine Learning",
    "cs.SY": "CS - Systems and Control",
    "stat.ML": "Stat - Machine Learning",
    "stat.AP": "Stat - Applications",
    "stat.ME": "Stat - Methodology",
}

RSS_BASE = "https://export.arxiv.org/rss/{category}"
MAX_PAPERS = 10
DELAY_SECONDS = 3  # arxiv requests >=3 s between calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_ANNOUNCE_RE = re.compile(r"arXiv:\d+\.\d+v\d+\s+Announce Type:\s*\w+\s*")
_LATEX_ACCENT_RE = re.compile(r"\\\\'([a-zA-Z])")


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return unescape(_TAG_RE.sub("", text)).strip()


def clean_abstract(text: str) -> str:
    """Remove HTML, arxiv announce header, and clean up whitespace."""
    text = strip_html(text)
    text = _ANNOUNCE_RE.sub("", text)
    # Remove leading "Abstract: " if present
    if text.startswith("Abstract:"):
        text = text[len("Abstract:"):].strip()
    return text


def clean_authors(text: str) -> str:
    """Decode LaTeX accent escapes in author names."""
    text = _LATEX_ACCENT_RE.sub(r"\1", text)
    return text.replace("\\'", "'").replace('\\"', "")


def clean_title(raw: str) -> str:
    """Remove trailing arxiv ID from RSS title, e.g. '(arXiv:2301.12345v1 ...)'."""
    idx = raw.rfind("(arXiv:")
    if idx != -1:
        raw = raw[:idx]
    return raw.strip()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_category(category: str) -> list[dict]:
    """Return up to MAX_PAPERS new papers for *category* from the RSS feed."""
    url = RSS_BASE.format(category=category)
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()
    except Exception as exc:
        print(f"[WARN] Failed to fetch {category}: {exc}", file=sys.stderr)
        return []

    root = ElementTree.fromstring(xml_bytes)

    papers: list[dict] = []
    for item in root.iter("item"):
        # Filter: only brand-new submissions
        announce = ""
        for child in item:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "announce_type":
                announce = (child.text or "").strip()
                break
        if announce and announce != "new":
            continue

        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")

        # dc:creator uses a namespace
        creator_el = None
        for child in item:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "creator":
                creator_el = child
                break

        raw_title = (title_el.text if title_el is not None and title_el.text else "")
        papers.append(
            {
                "title": clean_title(raw_title),
                "link": (link_el.text.strip() if link_el is not None and link_el.text else ""),
                "abstract": clean_abstract(desc_el.text if desc_el is not None and desc_el.text else ""),
                "authors": clean_authors(creator_el.text.strip() if creator_el is not None and creator_el.text else ""),
                "category": category,
            }
        )
        if len(papers) >= MAX_PAPERS:
            break

    return papers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) > 1:
        categories = sys.argv[1:]
    else:
        categories = TOP_LEVEL + CS_SUBS + STAT_SUBS

    results: dict[str, dict] = {}
    for i, cat in enumerate(categories):
        print(f"Fetching {cat} … ({i + 1}/{len(categories)})", file=sys.stderr)
        papers = fetch_category(cat)
        results[cat] = {
            "label": CATEGORY_LABELS.get(cat, cat),
            "papers": papers,
        }
        if i < len(categories) - 1:
            time.sleep(DELAY_SECONDS)

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
