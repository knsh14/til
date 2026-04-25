#!/usr/bin/env python3
"""Fetch today's new papers from arxiv RSS feeds.

Usage:
    python fetch_arxiv.py                          # Use default categories.json
    python fetch_arxiv.py categories.json          # Use specified config
    python fetch_arxiv.py cs.AI cs.CV stat         # Fetch specific categories only

Output: JSON to stdout with papers grouped by category.
"""

import json
import os
import re
import sys
import time
import urllib.request
from html import unescape
from xml.etree import ElementTree


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RSS_BASE = "https://export.arxiv.org/rss/{category}"
MAX_PAPERS = 10
DELAY_SECONDS = 3  # arxiv requests >=3 s between calls
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry


# ---------------------------------------------------------------------------
# Default categories (used when no config file exists)
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = {
    "top_level": ["cs", "math", "physics", "stat", "eess", "q-bio", "q-fin", "econ"],
    "subcategories": ["cs.CV", "cs.RO", "cs.AI", "cs.LG", "cs.SY", "stat.ML", "stat.AP", "stat.ME"],
    "labels": {
        "cs": "Computer Science",
        "cs.CV": "CS - Computer Vision and Pattern Recognition",
        "cs.RO": "CS - Robotics",
        "cs.AI": "CS - Artificial Intelligence",
        "cs.LG": "CS - Machine Learning",
        "cs.SY": "CS - Systems and Control",
        "math": "Mathematics",
        "physics": "Physics",
        "stat": "Statistics",
        "stat.ML": "Stat - Machine Learning",
        "stat.AP": "Stat - Applications",
        "stat.ME": "Stat - Methodology",
        "eess": "Electrical Engineering and Systems Science",
        "q-bio": "Quantitative Biology",
        "q-fin": "Quantitative Finance",
        "econ": "Economics",
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_categories(path: str | None) -> tuple[list[str], dict[str, str]]:
    """Load categories from JSON config or use defaults.

    Returns (category_list, labels_dict).
    """
    if path and os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        cats = data.get("top_level", []) + data.get("subcategories", [])
        labels = data.get("labels", {})
        return cats, labels

    # Try default path relative to script
    default = os.path.join(os.path.dirname(__file__), "..", "categories.json")
    if os.path.exists(default):
        with open(default) as f:
            data = json.load(f)
        cats = data.get("top_level", []) + data.get("subcategories", [])
        labels = data.get("labels", {})
        return cats, labels

    # Fall back to hardcoded defaults
    cats = DEFAULT_CATEGORIES["top_level"] + DEFAULT_CATEGORIES["subcategories"]
    labels = DEFAULT_CATEGORIES["labels"]
    return cats, labels


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
    if text.startswith("Abstract:"):
        text = text[len("Abstract:"):].strip()
    return text


def clean_authors(text: str) -> str:
    """Decode LaTeX accent escapes in author names."""
    text = _LATEX_ACCENT_RE.sub(r"\1", text)
    return text.replace("\\'", "'").replace('\\"', "")


def clean_title(raw: str) -> str:
    """Remove trailing arxiv ID from RSS title."""
    idx = raw.rfind("(arXiv:")
    if idx != -1:
        raw = raw[:idx]
    return raw.strip()


# ---------------------------------------------------------------------------
# Fetch with retry
# ---------------------------------------------------------------------------


def fetch_url(url: str) -> bytes | None:
    """Fetch URL content with retry and exponential backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest/1.0"})
    backoff = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as exc:
            if attempt < MAX_RETRIES:
                print(f"[WARN] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {exc}", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
            else:
                print(f"[WARN] All {MAX_RETRIES} attempts failed for {url}: {exc}", file=sys.stderr)
                return None


def fetch_category(category: str) -> list[dict]:
    """Return up to MAX_PAPERS new papers for *category* from the RSS feed."""
    url = RSS_BASE.format(category=category)
    xml_bytes = fetch_url(url)
    if xml_bytes is None:
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
    config_path: str | None = None
    explicit_categories: list[str] = []

    # Parse args: either a .json config file or explicit category names
    for arg in sys.argv[1:]:
        if arg.endswith(".json"):
            config_path = arg
        else:
            explicit_categories.append(arg)

    if explicit_categories:
        categories = explicit_categories
        labels: dict[str, str] = DEFAULT_CATEGORIES["labels"]
    else:
        categories, labels = load_categories(config_path)

    if not categories:
        print("[ERROR] No categories configured", file=sys.stderr)
        sys.exit(1)

    results: dict[str, dict] = {}
    seen_links: set[str] = set()
    for i, cat in enumerate(categories):
        print(f"Fetching {cat} … ({i + 1}/{len(categories)})", file=sys.stderr)
        papers = fetch_category(cat)

        # Drop papers already emitted for an earlier category so subcategory
        # digests only cover papers not in their parent category.
        unique_papers = []
        for p in papers:
            link = p.get("link", "")
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            unique_papers.append(p)

        results[cat] = {
            "label": labels.get(cat, cat),
            "papers": unique_papers,
        }
        if i < len(categories) - 1:
            time.sleep(DELAY_SECONDS)

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
