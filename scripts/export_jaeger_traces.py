#!/usr/bin/env python3
"""Export traces from Jaeger Query API to a pretty JSON file."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_JAEGER_URL = "http://127.0.0.1:16686"
DEFAULT_OUTPUT_DIR = Path("artifacts/traces")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jaeger-url",
        default=DEFAULT_JAEGER_URL,
        help=f"Jaeger UI/API URL, default: {DEFAULT_JAEGER_URL}",
    )
    parser.add_argument("--service", default="sumbot", help="Service name for trace search.")
    parser.add_argument("--lookback", default="1h", help="Jaeger lookback value, e.g. 15m, 1h, 24h.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum traces to export.")
    parser.add_argument("--operation", default="", help="Optional operation/span name filter.")
    parser.add_argument("--trace-id", default="", help="Export one exact trace id instead of search results.")
    parser.add_argument(
        "--slowest",
        action="store_true",
        help="From search results, export only the trace with the longest span duration.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path.")
    return parser.parse_args()


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Jaeger API returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect to Jaeger API: {exc.reason}") from exc


def build_url(args: argparse.Namespace) -> str:
    base_url = args.jaeger_url.rstrip("/")
    if args.trace_id:
        return f"{base_url}/api/traces/{args.trace_id}"

    query: dict[str, str | int] = {
        "service": args.service,
        "lookback": args.lookback,
        "limit": args.limit,
    }
    if args.operation:
        query["operation"] = args.operation
    return f"{base_url}/api/traces?{urlencode(query)}"


def default_output_path(args: argparse.Namespace) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.trace_id:
        filename = f"jaeger-trace-{args.trace_id}-{timestamp}.json"
    elif args.slowest:
        service = args.service.replace("/", "_").replace(":", "_")
        filename = f"jaeger-slowest-trace-{service}-{args.lookback}-{timestamp}.json"
    else:
        service = args.service.replace("/", "_").replace(":", "_")
        filename = f"jaeger-traces-{service}-{args.lookback}-{timestamp}.json"
    return DEFAULT_OUTPUT_DIR / filename


def trace_duration_us(trace: dict[str, Any]) -> int:
    spans = trace.get("spans", [])
    return max((int(span.get("duration") or 0) for span in spans), default=0)


def select_slowest_trace(payload: dict[str, Any]) -> dict[str, Any]:
    traces = payload.get("data", [])
    if not traces:
        return payload

    slowest_trace = max(traces, key=trace_duration_us)
    payload = dict(payload)
    payload["data"] = [slowest_trace]
    return payload


def main() -> int:
    args = parse_args()
    url = build_url(args)
    output_path = args.output or default_output_path(args)

    payload = fetch_json(url)
    if args.slowest and not args.trace_id:
        payload = select_slowest_trace(payload)

    traces = payload.get("data", [])
    slowest_duration_us = max((trace_duration_us(trace) for trace in traces), default=0)
    exported = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "query": {
            "jaeger_url": args.jaeger_url,
            "service": args.service,
            "lookback": args.lookback,
            "limit": args.limit,
            "operation": args.operation,
            "trace_id": args.trace_id,
            "slowest": args.slowest,
        },
        "slowest_duration_seconds": slowest_duration_us / 1_000_000,
        "trace_count": len(traces),
        "jaeger": payload,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(exported, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Exported {exported['trace_count']} trace(s) "
        f"(slowest {exported['slowest_duration_seconds']:.3f}s) to {output_path}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
