#!/usr/bin/env python3
"""Fetch recent Issue/PR activity from GitHub repositories.

Usage:
    python fetch_github.py repos.json              # Fetch all repos in config
    python fetch_github.py --repo owner/repo        # Fetch a single repo

Output: JSON to stdout with items grouped by repository.
Progress and errors go to stderr.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ISSUES = 50
MAX_PRS = 50
MAX_COMMENTS_PAGES = 3  # max pages of comments to fetch (300 comments)


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------


def run_gh_api(endpoint: str) -> dict | list | None:
    """Call gh api for a single request. Returns parsed JSON or None on error."""
    cmd = [
        "gh", "api", endpoint,
        "-H", "Accept: application/vnd.github+json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print(f"[WARN] Timeout: {endpoint}", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"[WARN] gh api failed ({result.returncode}): {endpoint}", file=sys.stderr)
        print(f"       {result.stderr.strip()}", file=sys.stderr)
        return None

    return json.loads(result.stdout)


def run_gh_api_paginated(endpoint: str) -> list:
    """Call gh api --paginate for endpoints that return arrays.

    gh --paginate concatenates JSON arrays as [...][...] which is not valid
    JSON. We use JSONDecoder.raw_decode() to parse each fragment.
    """
    cmd = [
        "gh", "api", endpoint,
        "-H", "Accept: application/vnd.github+json",
        "--paginate",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"[WARN] Timeout (paginated): {endpoint}", file=sys.stderr)
        return []

    if result.returncode != 0:
        print(f"[WARN] gh api paginated failed ({result.returncode}): {endpoint}", file=sys.stderr)
        print(f"       {result.stderr.strip()}", file=sys.stderr)
        return []

    text = result.stdout.strip()
    if not text:
        return []

    items = []
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        if isinstance(obj, list):
            items.extend(obj)
        else:
            items.append(obj)
        pos = end
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1

    return items


# ---------------------------------------------------------------------------
# GitHub data fetching
# ---------------------------------------------------------------------------


def get_since_timestamp() -> str:
    """Return ISO8601 timestamp for 24 hours ago."""
    dt = datetime.now(timezone.utc) - timedelta(hours=24)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_issues_and_prs(repo: str, since: str) -> list[dict]:
    """Fetch issues and PRs updated since the given timestamp.

    Caps at MAX_ISSUES issues and MAX_PRS pull requests, both sorted
    by updated_at descending (most recent first).
    """
    issues: list[dict] = []
    prs: list[dict] = []
    page = 1
    while len(issues) < MAX_ISSUES or len(prs) < MAX_PRS:
        endpoint = (
            f"/repos/{repo}/issues"
            f"?state=all&sort=updated&direction=desc&per_page=100"
            f"&since={since}&page={page}"
        )
        batch = run_gh_api(endpoint)
        if not batch:
            break
        for item in batch:
            if "pull_request" in item:
                if len(prs) < MAX_PRS:
                    prs.append(item)
            else:
                if len(issues) < MAX_ISSUES:
                    issues.append(item)
        if len(batch) < 100:
            break  # last page
        page += 1
    return issues + prs


def fetch_comments(repo: str, since: str) -> list[dict]:
    """Fetch recent issue comments with page limit."""
    all_comments: list[dict] = []
    for page in range(1, MAX_COMMENTS_PAGES + 1):
        endpoint = (
            f"/repos/{repo}/issues/comments"
            f"?sort=created&direction=desc&per_page=100&since={since}&page={page}"
        )
        batch = run_gh_api(endpoint)
        if not batch:
            break
        all_comments.extend(batch)
        if len(batch) < 100:
            break
    return all_comments


def fetch_pr_details(repo: str, number: int) -> dict | None:
    """Fetch PR-specific details (merge status, additions, deletions)."""
    return run_gh_api(f"/repos/{repo}/pulls/{number}")


def fetch_pr_files(repo: str, number: int) -> list:
    """Fetch changed files for a PR (first page only, max 100)."""
    return run_gh_api(f"/repos/{repo}/pulls/{number}/files?per_page=100") or []


def fetch_pr_reviews(repo: str, number: int) -> list:
    """Fetch reviews for a PR (first page only)."""
    return run_gh_api(f"/repos/{repo}/pulls/{number}/reviews?per_page=100") or []


def fetch_repo(repo: str, since: str) -> dict:
    """Fetch all recent activity for a single repository.

    Returns {"items": [...], "error": None} or {"items": [], "error": "..."}.
    """
    print(f"Fetching {repo} …", file=sys.stderr)

    # 1. Get issues and PRs updated in the last 24h
    raw_items = fetch_issues_and_prs(repo, since)
    if raw_items is None:
        return {"items": [], "error": f"Failed to fetch issues for {repo}"}

    # Client-side filter: keep only items with updated_at >= since
    raw_items = [
        item for item in raw_items
        if item.get("updated_at", "") >= since
    ]

    # 2. Get comments from the last 24h and group by issue number
    all_comments = fetch_comments(repo, since)
    comments_by_number: dict[int, list] = {}
    for c in all_comments:
        issue_url = c.get("issue_url", "")
        try:
            num = int(issue_url.rstrip("/").rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue
        comments_by_number.setdefault(num, []).append({
            "user": (c.get("user") or {}).get("login", "unknown"),
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
        })

    # 3. Process each item
    items = []
    pr_count = 0
    for raw in raw_items:
        number = raw["number"]
        is_pr = "pull_request" in raw

        item = {
            "number": number,
            "title": raw.get("title", ""),
            "html_url": raw.get("html_url", ""),
            "type": "pull_request" if is_pr else "issue",
            "state": raw.get("state", "unknown"),
            "user": (raw.get("user") or {}).get("login", "unknown"),
            "labels": [l.get("name", "") for l in (raw.get("labels") or [])],
            "body": raw.get("body", "") or "",
            "created_at": raw.get("created_at", ""),
            "updated_at": raw.get("updated_at", ""),
            "comments": comments_by_number.get(number, []),
            "pr_details": None,
            "files": None,
            "reviews": None,
        }

        # 4. Fetch PR-specific data (details + files + reviews, skip review_comments)
        if is_pr:
            pr_count += 1
            print(f"  PR {pr_count}: #{number} …", file=sys.stderr, end="\r")

            pr = fetch_pr_details(repo, number)
            if pr:
                item["pr_details"] = {
                    "merged": pr.get("merged", False),
                    "draft": pr.get("draft", False),
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                    "changed_files": pr.get("changed_files", 0),
                    "base_branch": (pr.get("base") or {}).get("ref", ""),
                    "head_branch": (pr.get("head") or {}).get("ref", ""),
                }
                if pr.get("merged"):
                    item["state"] = "merged"

            item["files"] = [
                {
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                }
                for f in fetch_pr_files(repo, number)
            ]

            item["reviews"] = [
                {
                    "user": (r.get("user") or {}).get("login", "unknown"),
                    "state": r.get("state", ""),
                    "body": r.get("body", "") or "",
                    "submitted_at": r.get("submitted_at", ""),
                }
                for r in fetch_pr_reviews(repo, number)
            ]

        items.append(item)

    # Sort by updated_at descending
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

    issue_count = sum(1 for i in items if i["type"] == "issue")
    pr_total = sum(1 for i in items if i["type"] == "pull_request")
    print(f"  {repo}: {len(items)} items ({pr_total} PRs, {issue_count} issues)", file=sys.stderr)

    return {"items": items, "error": None}


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------


def check_auth() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except FileNotFoundError:
        print("[ERROR] gh CLI not found. Install: https://cli.github.com/", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("[ERROR] gh auth status timed out", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: str) -> list[str]:
    """Load repos.json and return list of owner/repo strings."""
    with open(path) as f:
        data = json.load(f)
    repos = data.get("repos", [])
    if not repos:
        print("[ERROR] No repos found in config", file=sys.stderr)
        sys.exit(1)
    return repos


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Parse arguments
    repos: list[str] = []
    config_path: str | None = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--repo" and i + 1 < len(args):
            repos.append(args[i + 1])
            i += 2
        else:
            config_path = args[i]
            i += 1

    if not repos and not config_path:
        # Try default path relative to script
        default = os.path.join(os.path.dirname(__file__), "..", "repos.json")
        if os.path.exists(default):
            config_path = default
        else:
            print("Usage: fetch_github.py [repos.json] [--repo owner/repo]", file=sys.stderr)
            sys.exit(1)

    if not repos and config_path:
        repos = load_config(config_path)

    # Auth check
    if not check_auth():
        sys.exit(1)

    since = get_since_timestamp()
    print(f"Fetching activity since {since}", file=sys.stderr)

    # Fetch each repo
    results: dict[str, dict] = {}
    for idx, repo in enumerate(repos):
        results[repo] = fetch_repo(repo, since)
        if idx < len(repos) - 1:
            time.sleep(1)

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "repos": results,
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
