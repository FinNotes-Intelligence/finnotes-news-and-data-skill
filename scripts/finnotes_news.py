#!/usr/bin/env python3
"""Fast FinNotes news helper for agents.

Default mode returns list metadata only. The API key stays in credentials.env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CREDENTIALS = Path.home() / ".finnotes" / "credentials.env"
DEFAULT_API_BASE = "https://api.finnotes.com/v1"
DEFAULT_USER_AGENT = "FinNotes-Agent-Skill/0.1 (+https://platform.finnotes.com)"


class RequestError(Exception):
    def __init__(self, message: str, body: str = "") -> None:
        super().__init__(message)
        self.body = body


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_credentials(path: Path) -> tuple[str, str]:
    file_values = _read_env_file(path)
    api_key = os.environ.get("FINNOTES_API_KEY") or file_values.get("FINNOTES_API_KEY") or ""
    api_base = os.environ.get("FINNOTES_API_BASE") or file_values.get("FINNOTES_API_BASE") or DEFAULT_API_BASE
    api_key = api_key.strip()
    api_base = api_base.strip().rstrip("/")
    if not api_key:
        print("NO_KEY: read references/create-api-key-guide.md", file=sys.stderr)
        raise SystemExit(3)
    if not api_key.startswith("fnp_"):
        print("INVALID_KEY_FORMAT: read references/create-api-key-guide.md", file=sys.stderr)
        raise SystemExit(3)
    return api_base, api_key


def _request(api_base: str, api_key: str, path: str, quiet: bool = False) -> dict[str, Any]:
    request = urllib.request.Request(
        api_base + path,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        message = f"HTTP {exc.code}"
        if exc.code == 403 and "cloudflare" in error_body.lower():
            message = "CLOUDFLARE_ACCESS_DENIED: request signature blocked before FinNotes API handled the request"
        elif exc.code == 401:
            message = "KEY_REJECTED: read references/create-api-key-guide.md"
        elif exc.code == 403:
            message = "PERMISSION_DENIED: read references/create-api-key-guide.md"
        elif exc.code == 402 or "insufficient" in error_body.lower():
            message = "POINTS_OR_QUOTA_PROBLEM: read references/create-api-key-guide.md"
        if quiet:
            raise RequestError(message, error_body) from None
        print(message, file=sys.stderr)
        if error_body:
            print(error_body)
        raise SystemExit(4) from None
    except urllib.error.URLError as exc:
        if quiet:
            raise RequestError(f"NETWORK_ERROR: {exc}") from None
        print(f"NETWORK_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8) from None

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        print(payload)
        raise SystemExit(0) from None


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _summary_path(days: int, limit: int, news_type: str) -> str:
    if days < 1:
        raise SystemExit("Days must be >= 1")
    query: dict[str, str] = {"type": news_type, "limit": str(limit)}
    if days == 1:
        query["range"] = "today"
    else:
        query["last_days"] = str(days)
    return "/news?" + urllib.parse.urlencode(query)


def _date_summary_path(date: str, limit: int, news_type: str) -> str:
    query = {"date": date, "type": news_type, "limit": str(limit)}
    return "/news?" + urllib.parse.urlencode(query)


def _detail_path(identifier: str, slug: str | None) -> str:
    if slug:
        news_type = identifier
        if news_type not in {"market-news", "chart-news"}:
            raise SystemExit("For type+slug detail lookup, type must be market-news or chart-news")
        return f"/news/{news_type}/{urllib.parse.quote(slug)}?include_references=true&include_chart=true"
    return f"/news/{urllib.parse.quote(identifier)}?include_references=true&include_chart=true"


def _full_today_path(news_type: str) -> str:
    query = "" if news_type in ("", "all") else "?" + urllib.parse.urlencode({"type": news_type})
    return "/news/today/full" + query


def _validate_news_type_filter(value: str) -> str:
    allowed = {"all", "market-news", "chart-news", "column-article"}
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return "all"
    if "all" in parts and len(parts) > 1:
        raise SystemExit("--type cannot combine all with specific types")
    bad = set(parts) - allowed
    if bad:
        raise SystemExit(f"Unsupported --type value(s): {', '.join(sorted(bad))}")
    return ",".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch FinNotes news quickly.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="1",
        help="Day count for summaries; date for a specific-date summary; full for today's full details; detail for one item",
    )
    parser.add_argument("value", nargs="?", help="News id for detail mode, or type followed by slug")
    parser.add_argument("slug", nargs="?", help="Slug for detail mode")
    parser.add_argument("--limit", type=int, default=50, help="Summary list size, max 100")
    parser.add_argument("--type", default="all", help="News type filter; supports comma-separated values")
    parser.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS), help="Path to credentials.env.")
    args = parser.parse_args()

    api_base, api_key = _load_credentials(Path(args.credentials).expanduser())
    news_type = _validate_news_type_filter(args.type)

    if args.mode == "full":
        _print_json(_request(api_base, api_key, _full_today_path(news_type)))
        return 0

    if args.mode == "detail":
        if not args.value:
            raise SystemExit("detail mode requires a news id, or type + slug")
        _print_json(_request(api_base, api_key, _detail_path(args.value, args.slug)))
        return 0

    if args.mode == "date":
        if not args.value:
            raise SystemExit("date mode requires YYYY-MM-DD")
        _print_json(_request(api_base, api_key, _date_summary_path(args.value, max(1, min(args.limit, 100)), news_type)))
        return 0

    try:
        days = int(args.mode)
    except ValueError:
        raise SystemExit("Mode must be a day count, date, full, or detail") from None

    limit = max(1, min(args.limit, 100))
    _print_json(_request(api_base, api_key, _summary_path(days, limit, news_type)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
