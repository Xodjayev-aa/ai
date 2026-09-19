"""Find the Vercel *preview* URL built for a specific commit.

Used by .github/workflows/e2e-preview.yml so the end-to-end suite always runs
against the deployment that the push actually produced (no guessing at URLs).

Usage:
    python tests/wait_for_preview.py --repo owner/name --sha <sha> \
        --github-output "$GITHUB_OUTPUT" [--timeout 900]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def _get(path: str, token: str) -> object:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aether-e2e",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def candidate_urls(repo: str, sha: str, token: str) -> list[str]:
    """Every deployment URL GitHub knows about for this commit, newest first."""
    urls: list[str] = []
    try:
        deployments = _get(f"/repos/{repo}/deployments?sha={sha}&per_page=50", token)
    except urllib.error.HTTPError as exc:
        print(f"deployments lookup failed: {exc}", file=sys.stderr)
        return urls
    if not isinstance(deployments, list):
        return urls
    for dep in deployments:
        try:
            statuses = _get(f"/repos/{repo}/deployments/{dep['id']}/statuses", token)
        except urllib.error.HTTPError:
            continue
        if not isinstance(statuses, list):
            continue
        for st in statuses:
            url = st.get("environment_url") or ""
            if url and st.get("state") == "success" and url not in urls:
                urls.append(url)
    return urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--github-output", default=os.getenv("GITHUB_OUTPUT", ""))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--print-url-only", action="store_true",
                    help="print just the URL on stdout (for CI shell capture)")
    args = ap.parse_args()

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    if not token:
        print("no GitHub token in env", file=sys.stderr)
        return 1

    deadline = time.time() + args.timeout
    last: list[str] = []
    while time.time() < deadline:
        urls = candidate_urls(args.repo, args.sha, token)
        if urls != last:
            print(f"candidate deployment URLs: {urls}")
            last = urls
        # Prefer a preview URL; the project's own production domain is fine too
        # if Vercel only produced a production deployment for this commit.
        if urls:
            url = urls[0]
            if args.print_url_only:
                print(url)
                return 0
            if args.github_output:
                with open(args.github_output, "a", encoding="utf-8") as fh:
                    fh.write(f"base_url={url}\n")
            print(f"using {url}")
            return 0
        time.sleep(15)

    print("timed out waiting for a Vercel deployment", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
