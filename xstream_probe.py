#!/usr/bin/env python3
"""Check which Xtream Codes servers accept one account.

The script tests a single username/password pair against a list of server
base URLs (mirrors), reports which ones are reachable and authorized, and
optionally saves the working URLs to a file.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

__version__ = "1.0.0"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_URLS_FILE = SCRIPT_DIR / "data" / "urls.txt"
DEFAULT_OUTPUT_FILE = Path("working_urls.csv")
DEFAULT_TIMEOUT = 10.0
USER_AGENT = f"xstream-probe/{__version__}"

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
GRAY = "\033[0;90m"
RESET = "\033[0m"

STATUS_LABELS = {
    "ok": ("OK", GREEN),
    "auth-fail": ("AUTH-FAIL", YELLOW),
    "down": ("DOWN", RED),
    "unknown": ("UNKNOWN", GRAY),
}

CSV_HEADERS = {"url", "urls", "server", "servers", "base_url", "base url"}


@dataclass
class CheckResult:
    """Outcome of checking one server URL."""

    url: str
    status: str
    auth: Optional[str] = None
    account_status: Optional[str] = None
    expires: Optional[str] = None
    error: Optional[str] = None


def normalize_url(raw_url: str) -> str:
    """Trim whitespace and quotes and remove trailing slashes."""
    return raw_url.strip().strip("\"'").strip().rstrip("/")


def is_valid_url(url: str) -> bool:
    """Return True when the URL is an absolute HTTP(S) URL."""
    parts = urlsplit(url)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def url_key(url: str) -> Tuple[str, str, str, str]:
    """Build a case-insensitive key used to deduplicate server URLs."""
    parts = urlsplit(url)
    return (
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        parts.query,
    )


def load_urls(path: Path) -> List[str]:
    """Load, normalize, validate and deduplicate URLs from a text or CSV file."""
    if not path.is_file():
        raise FileNotFoundError(f"URL list file not found: {path}")

    entries: List[str] = []

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                if row:
                    entries.append(row[0])
    else:
        with path.open("r", encoding="utf-8-sig") as handle:
            entries.extend(handle.read().splitlines())

    urls: List[str] = []
    seen = set()
    skipped = 0

    for index, entry in enumerate(entries, start=1):
        stripped = entry.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.strip("\"'").strip().lower() in CSV_HEADERS:
            continue

        url = normalize_url(stripped)
        if not is_valid_url(url):
            skipped += 1
            print(f"Skipping invalid URL on line {index}: {stripped}", file=sys.stderr)
            continue

        key = url_key(url)
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)

    if skipped:
        print(f"Skipped {skipped} invalid URL(s).", file=sys.stderr)

    return urls


def build_api_url(base_url: str, username: str, password: str) -> str:
    """Build the player_api.php URL for one server."""
    query = urlencode({"username": username, "password": password})
    return f"{base_url}/player_api.php?{query}"


def format_expiry(value: Any) -> Optional[str]:
    """Convert a Unix timestamp into an ISO date, ignoring invalid values."""
    if value is None:
        return None
    try:
        timestamp = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def fetch_player_api(
    base_url: str,
    username: str,
    password: str,
    timeout: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch and decode player_api.php, returning payload or a short error."""
    request = Request(
        build_api_url(base_url, username, password),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (TimeoutError, socket.timeout):
        return None, "timed out"
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return None, "timed out"
        return None, str(reason) or "connection failed"
    except OSError as exc:
        return None, str(exc) or "connection failed"

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid JSON response"

    if not isinstance(payload, dict):
        return None, "unexpected response"

    return payload, None


def evaluate_response(
    url: str,
    payload: Optional[Dict[str, Any]],
    error: Optional[str] = None,
) -> CheckResult:
    """Classify a player_api.php response."""
    if error is not None:
        return CheckResult(url=url, status="down", error=error)

    if not isinstance(payload, dict):
        return CheckResult(url=url, status="unknown", error="unexpected response")

    user_info = payload.get("user_info")
    if not isinstance(user_info, dict):
        return CheckResult(url=url, status="unknown", error="missing user_info")

    auth_raw = user_info.get("auth")
    if auth_raw is None:
        return CheckResult(url=url, status="unknown", error="missing auth field")

    if isinstance(auth_raw, bool):
        auth = "1" if auth_raw else "0"
    else:
        auth = str(auth_raw).strip()

    account_status = user_info.get("status")
    account_status = str(account_status) if account_status is not None else None
    expires = format_expiry(user_info.get("exp_date"))

    result = CheckResult(
        url=url,
        status="unknown",
        auth=auth,
        account_status=account_status,
        expires=expires,
    )

    if auth == "1":
        if account_status is None or account_status.lower() == "active":
            result.status = "ok"
        else:
            result.status = "auth-fail"
    elif auth == "0":
        result.status = "auth-fail"
    else:
        result.error = f"unexpected auth value: {auth}"

    return result


def format_result(result: CheckResult, use_color: bool) -> str:
    """Format one result as a terminal line."""
    label, color = STATUS_LABELS[result.status]
    tag = f"[{label}]"
    details: List[str] = []

    if result.status == "ok":
        if result.account_status:
            details.append(result.account_status)
        if result.expires:
            details.append(f"expires {result.expires}")
    elif result.status == "auth-fail":
        if result.account_status:
            details.append(f"status: {result.account_status}")
        elif result.auth == "0":
            details.append("invalid credentials")
    elif result.error:
        details.append(result.error)

    extra = f"  ({', '.join(details)})" if details else ""
    if use_color:
        return f"{color}{tag:<12}{RESET} {result.url}{extra}"
    return f"{tag:<12} {result.url}{extra}"


def write_working_urls(path: Path, results: Sequence[CheckResult]) -> List[str]:
    """Write the URLs that returned OK, deduplicated, one per line."""
    urls: List[str] = []
    seen = set()

    for result in results:
        if result.status != "ok":
            continue
        key = url_key(result.url)
        if key in seen:
            continue
        seen.add(key)
        urls.append(result.url)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for url in urls:
            handle.write(f"{url}\n")

    return urls


def use_color_output(disabled: bool) -> bool:
    """Decide whether ANSI colors should be emitted."""
    return (
        not disabled
        and "NO_COLOR" not in os.environ
        and sys.stdout.isatty()
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="xstream_probe.py",
        description=(
            "Check which Xtream Codes server URLs accept one account and "
            "save the working ones."
        ),
        epilog=(
            "Credentials can also be supplied through the XSTREAM_USERNAME "
            "and XSTREAM_PASSWORD environment variables."
        ),
    )
    parser.add_argument("--username", help="Xtream Codes username")
    parser.add_argument("--password", help="Xtream Codes password")
    parser.add_argument(
        "--urls",
        type=Path,
        default=DEFAULT_URLS_FILE,
        help=f"URL list file (.txt or .csv, default: {DEFAULT_URLS_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"output file for working URLs (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-server timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable colored terminal output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the checker and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    username = args.username or os.environ.get("XSTREAM_USERNAME")
    if not username:
        parser.error("a username is required (--username or XSTREAM_USERNAME)")

    password = args.password
    if password is None:
        password = os.environ.get("XSTREAM_PASSWORD")
    if password is None:
        password = getpass.getpass("Password: ")
    if password == "":
        parser.error("password must not be empty")

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    try:
        urls = load_urls(args.urls)
    except (FileNotFoundError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not urls:
        print(f"Error: no valid URLs found in {args.urls}", file=sys.stderr)
        return 2

    color = use_color_output(args.no_color)
    print(f"Checking {len(urls)} server(s) from {args.urls}")
    print()

    results: List[CheckResult] = []
    try:
        for url in urls:
            payload, error = fetch_player_api(
                url, username, password, args.timeout
            )
            result = evaluate_response(url, payload, error)
            results.append(result)
            print(format_result(result, color), flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    working = write_working_urls(args.output, results)
    ok_count = sum(1 for result in results if result.status == "ok")

    print()
    print(f"{ok_count}/{len(results)} servers OK")
    print(f"Working URLs saved to: {args.output} ({len(working)} unique)")

    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
