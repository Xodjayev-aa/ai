"""Post (or update) the sticky E2E report comment on the pull request.

Reads `e2e-report.md` (written by tests/e2e_preview.py) and `probe.txt`
(written by tests/probe_pollinations.py) and upserts one comment, so repeated
pushes do not spam the PR.

Usage:
    python tests/post_report.py [pr_number]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

MARKER = "<!-- aether-e2e-report -->"
MAX_BODY = 60000


def gh_api(path: str, method: str = "GET", payload: dict | None = None) -> object:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "aether-e2e",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        body = resp.read().decode()
    return json.loads(body) if body else None


def read(path: str, limit: int = 20000) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()[:limit]
    except OSError:
        return ""


def tail(path: str, lines: int = 60, limit: int = 12000) -> str:
    text = read(path, limit=400000)
    if not text:
        return ""
    return "\n".join(text.splitlines()[-lines:])[:limit]


def main() -> int:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    sha = os.getenv("GITHUB_SHA", "")[:8]
    branch = os.getenv("GITHUB_REF_NAME", "")
    if len(sys.argv) > 1 and sys.argv[1].strip():
        pr_number = sys.argv[1].strip()
    else:
        try:
            prs = gh_api(f"/repos/{repo}/pulls?head={os.getenv('GITHUB_REPOSITORY_OWNER')}:{branch}&state=open")
            pr_number = str(prs[0]["number"]) if isinstance(prs, list) and prs else ""
        except Exception:  # noqa: BLE001
            pr_number = ""

    report = read("e2e-report.md", 30000)
    probe = tail("probe.txt", 80, 12000)
    server_log = tail("server.log", 40, 6000)

    body = [MARKER, f"## Keyless E2E — `{sha}` on `{branch}`", ""]
    body.append(report or "_the suite produced no report_")
    if probe:
        body += ["", "<details><summary>Keyless provider probe</summary>", "",
                 "```", probe.strip(), "```", "</details>"]
    if server_log:
        body += ["", "<details><summary>Server log tail</summary>", "",
                 "```", server_log.strip(), "```", "</details>"]
    text = "\n".join(body)[:MAX_BODY]

    if not (repo and pr_number):
        print("no pull request for this branch — report follows:\n")
        print(text)
        return 0

    existing = gh_api(f"/repos/{repo}/issues/{pr_number}/comments?per_page=100")
    target = None
    if isinstance(existing, list):
        for comment in existing:
            if MARKER in (comment.get("body") or ""):
                target = comment["id"]
                break
    if target:
        gh_api(f"/repos/{repo}/issues/comments/{target}", "PATCH", {"body": text})
        print(f"updated comment {target} on PR #{pr_number}")
    else:
        gh_api(f"/repos/{repo}/issues/{pr_number}/comments", "POST", {"body": text})
        print(f"created report comment on PR #{pr_number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
