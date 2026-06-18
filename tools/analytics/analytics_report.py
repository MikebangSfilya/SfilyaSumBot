import argparse
import asyncio
import csv
import io
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from sqlalchemy import BigInteger, DateTime, String, bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine


PERIOD_PATTERN = re.compile(r"^(?P<value>[1-9]\d*)(?P<unit>[hdw])$")
REPORT_FORMATS = ("text", "json", "csv")
LOW_CONFIDENCE_FEEDBACK_COUNT = 2
LOW_CONFIDENCE_COVERAGE_PCT = 30.0
OUTLIER_LIMIT = 5
MODEL_PRICE_USD_PER_1M_TOKENS = {
    "deepseek/deepseek-v4-flash": (0.09, 0.18),
    "google/gemma-4-26b-a4b-it": (0.06, 0.33),
    "qwen/qwen-2.5-7b-instruct": (0.04, 0.10),
}
GEMMA_BASELINE_MODEL = "google/gemma-4-26b-a4b-it"

REPORT_QUERY_PARAMS = (
    bindparam("since", type_=DateTime()),
    bindparam("model", type_=String()),
    bindparam("chat_id", type_=BigInteger()),
)

SUMMARY_REPORT_QUERY = text("""
    SELECT sl.id,
           sl.chat_id,
           COALESCE(NULLIF(BTRIM(bc.title), ''), 'Unknown chat') AS chat_title,
           sl.model_name,
           sl.style_id,
           sl.tone_id,
           sl.aggressiveness,
           COALESCE(NULLIF(BTRIM(sl.trigger_source), ''), 'manual') AS trigger_source,
           sl.input_tokens,
           sl.output_tokens,
           sl.summary_duration_seconds,
           sl.llm_duration_seconds,
           sl.created_at
    FROM summary_logs sl
    LEFT JOIN bot_chats bc ON bc.chat_id = sl.chat_id
    WHERE (:since IS NULL OR sl.created_at >= :since)
      AND (:model IS NULL OR sl.model_name = :model)
      AND (:chat_id IS NULL OR sl.chat_id = :chat_id)
    ORDER BY sl.created_at ASC, sl.id ASC
""").bindparams(*REPORT_QUERY_PARAMS)

FEEDBACK_REPORT_QUERY = text("""
    SELECT sf.summary_log_id,
           sf.chat_id,
           COALESCE(NULLIF(BTRIM(bc.title), ''), 'Unknown chat') AS chat_title,
           sf.feedback_value,
           sf.sentiment,
           sf.details,
           sf.created_at,
           sl.model_name,
           sl.style_id,
           sl.tone_id,
           sl.aggressiveness,
           COALESCE(NULLIF(BTRIM(sl.trigger_source), ''), 'manual') AS trigger_source
    FROM summary_feedback sf
    JOIN summary_logs sl ON sl.id = sf.summary_log_id
    LEFT JOIN bot_chats bc ON bc.chat_id = sf.chat_id
    WHERE (:since IS NULL OR sl.created_at >= :since)
      AND (:model IS NULL OR sl.model_name = :model)
      AND (:chat_id IS NULL OR sl.chat_id = :chat_id)
    ORDER BY sf.created_at ASC, sf.id ASC
""").bindparams(*REPORT_QUERY_PARAMS)


def parse_period(value: str) -> timedelta | None:
    normalized = value.strip().lower()
    if normalized == "all":
        return None
    match = PERIOD_PATTERN.fullmatch(normalized)
    if not match:
        raise ValueError("period must be 'all' or a positive value such as 24h, 7d, or 4w")

    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percentile_value
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


async def fetch_report_rows(
    db_url: str,
    since: datetime | None,
    model: str | None = None,
    chat_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = create_async_engine(db_url)
    query_since = since.astimezone(timezone.utc).replace(tzinfo=None) if since else None
    params = {"since": query_since, "model": model, "chat_id": chat_id}
    try:
        async with engine.connect() as conn:
            summary_result = await conn.execute(SUMMARY_REPORT_QUERY, params)
            feedback_result = await conn.execute(FEEDBACK_REPORT_QUERY, params)
            summary_rows = [dict(row._mapping) for row in summary_result.fetchall()]
            feedback_rows = [dict(row._mapping) for row in feedback_result.fetchall()]
        return summary_rows, feedback_rows
    finally:
        await engine.dispose()


def build_analytics_report(
    summary_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
    *,
    period: str,
    report_timezone: ZoneInfo,
    generated_at: datetime | None = None,
    since: datetime | None = None,
    model: str | None = None,
    chat_id: int | None = None,
    negative_details_limit: int = 10,
) -> dict[str, Any]:
    generated_at = _as_utc(generated_at or datetime.now(timezone.utc))
    summaries_by_id = {row["id"]: row for row in summary_rows}
    chat_filter = _resolve_chat_filter_label(summary_rows, feedback_rows, chat_id)

    report = {
        "metadata": {
            "generated_at": generated_at.isoformat(),
            "period": period,
            "since": _as_utc(since).isoformat() if since else None,
            "timezone": str(report_timezone),
            "model_filter": model,
            "chat_filter": chat_filter,
        },
        "overall": _calculate_metrics(summary_rows, feedback_rows),
        "models": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: str(row.get("model_name") or "unknown"),
        ),
        "styles": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: str(row.get("style_id") or "legacy/unknown"),
        ),
        "tones": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: str(row.get("tone_id") or "legacy/unknown"),
        ),
        "aggressiveness": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: (
                str(row["aggressiveness"])
                if row.get("aggressiveness") is not None
                else "legacy/unknown"
            ),
        ),
        "sources": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: str(row.get("trigger_source") or "manual"),
        ),
        "chats": _build_chat_dimension(summary_rows, feedback_rows),
        "days": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: _local_datetime(row["created_at"], report_timezone).strftime("%Y-%m-%d"),
        ),
        "hours": _build_dimension(
            summary_rows,
            feedback_rows,
            lambda row: _local_datetime(row["created_at"], report_timezone).strftime("%H:00"),
        ),
        "failure_signals": _build_unavailable_failure_signals(),
        "negative_details": _latest_negative_details(
            feedback_rows,
            summaries_by_id,
            report_timezone,
            negative_details_limit,
        ),
        "negative_categories": _build_negative_categories_stats(feedback_rows),
    }
    report["outliers"] = _build_outliers(report)
    return report


def _build_dimension(
    summary_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
    key_builder: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    grouped_summaries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summary_keys: dict[int, str] = {}
    for row in summary_rows:
        key = key_builder(row)
        grouped_summaries[key].append(row)
        summary_keys[row["id"]] = key

    grouped_feedback: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in feedback_rows:
        key = summary_keys.get(row["summary_log_id"])
        if key is not None:
            grouped_feedback[key].append(row)

    return [
        {"key": key, **_calculate_metrics(rows, grouped_feedback.get(key, []))}
        for key, rows in sorted(grouped_summaries.items())
    ]


def _build_chat_dimension(
    summary_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped_summaries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    grouped_feedback: dict[int, list[dict[str, Any]]] = defaultdict(list)
    labels: dict[int, str] = {}

    for row in summary_rows:
        chat_id = int(row["chat_id"])
        grouped_summaries[chat_id].append(row)
        labels[chat_id] = _chat_title(row)
    for row in feedback_rows:
        grouped_feedback[int(row["chat_id"])].append(row)

    return [
        {"key": labels[chat_id], **_calculate_metrics(rows, grouped_feedback.get(chat_id, []))}
        for chat_id, rows in sorted(grouped_summaries.items(), key=lambda item: labels[item[0]].casefold())
    ]


def _calculate_metrics(
    summary_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary_ids = {row["id"] for row in summary_rows}
    relevant_feedback = [row for row in feedback_rows if row["summary_log_id"] in summary_ids]
    rated_summary_ids = {row["summary_log_id"] for row in relevant_feedback}
    sentiments = defaultdict(int)
    for row in relevant_feedback:
        sentiments[str(row.get("sentiment") or "unknown")] += 1

    durations = _numeric_values(summary_rows, "summary_duration_seconds")
    llm_durations = _numeric_values(summary_rows, "llm_duration_seconds")
    input_tokens = _numeric_values(summary_rows, "input_tokens")
    output_tokens = _numeric_values(summary_rows, "output_tokens")
    total_input_tokens = int(sum(input_tokens))
    total_output_tokens = int(sum(output_tokens))
    estimated_cost = _estimate_rows_cost(summary_rows)
    gemma_baseline_cost = _estimate_token_cost(
        GEMMA_BASELINE_MODEL,
        total_input_tokens,
        total_output_tokens,
    )
    feedback_count = len(relevant_feedback)
    summary_count = len(summary_rows)

    return {
        "summaries": summary_count,
        "rated_summaries": len(rated_summary_ids),
        "feedback": feedback_count,
        "feedback_coverage_pct": _percentage(len(rated_summary_ids), summary_count),
        "positive": sentiments["positive"],
        "neutral": sentiments["neutral"],
        "negative": sentiments["negative"],
        "positive_pct": _percentage(sentiments["positive"], feedback_count),
        "neutral_pct": _percentage(sentiments["neutral"], feedback_count),
        "negative_pct": _percentage(sentiments["negative"], feedback_count),
        "avg_summary_duration_seconds": _average(durations),
        "p50_summary_duration_seconds": _rounded(percentile(durations, 0.50)),
        "p95_summary_duration_seconds": _rounded(percentile(durations, 0.95)),
        "avg_llm_duration_seconds": _average(llm_durations),
        "p50_llm_duration_seconds": _rounded(percentile(llm_durations, 0.50)),
        "p95_llm_duration_seconds": _rounded(percentile(llm_durations, 0.95)),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "avg_input_tokens": _average(input_tokens),
        "avg_output_tokens": _average(output_tokens),
        "estimated_cost_usd": estimated_cost,
        "gemma_baseline_cost_usd": gemma_baseline_cost,
        "gemma_savings_usd": _rounded_cost(gemma_baseline_cost - estimated_cost)
        if estimated_cost is not None and gemma_baseline_cost is not None
        else None,
        "confidence": _confidence_label(len(rated_summary_ids), summary_count),
    }


def _latest_negative_details(
    feedback_rows: list[dict[str, Any]],
    summaries_by_id: dict[int, dict[str, Any]],
    report_timezone: ZoneInfo,
    limit: int,
) -> list[dict[str, Any]]:
    negative_rows = [
        row
        for row in feedback_rows
        if row.get("sentiment") == "negative" and str(row.get("details") or "").strip()
    ]
    negative_rows.sort(key=lambda row: _as_utc(row["created_at"]), reverse=True)

    result = []
    for row in negative_rows[:limit]:
        summary = summaries_by_id.get(row["summary_log_id"], {})
        result.append(
            {
                "created_at": _local_datetime(row["created_at"], report_timezone).isoformat(),
                "chat": _chat_title(row),
                "model": summary.get("model_name") or row.get("model_name") or "unknown",
                "source": summary.get("trigger_source") or row.get("trigger_source") or "manual",
                "category": _categorize_negative_details(str(row["details"])),
                "details": str(row["details"]).strip(),
            }
        )
    return result


def _build_outliers(report: dict[str, Any]) -> list[dict[str, Any]]:
    outliers = []
    for dimension in ("models", "styles", "tones", "chats"):
        for row in report[dimension]:
            if row["negative"] <= 0:
                continue
            outliers.append(
                {
                    "dimension": dimension,
                    "key": row["key"],
                    "summaries": row["summaries"],
                    "feedback": row["feedback"],
                    "feedback_coverage_pct": row["feedback_coverage_pct"],
                    "negative": row["negative"],
                    "negative_pct": row["negative_pct"],
                    "confidence": row["confidence"],
                }
            )
    outliers.sort(
        key=lambda row: (
            row["confidence"] == "low",
            -(row["negative_pct"] or 0),
            -row["negative"],
            str(row["dimension"]),
            str(row["key"]),
        )
    )
    return outliers[:OUTLIER_LIMIT]


def _build_unavailable_failure_signals() -> dict[str, dict[str, Any]]:
    return {
        "fallback_recoveries": {
            "available": False,
            "count": None,
            "note": "not stored in analytics rows yet",
        },
        "validator_rejections": {
            "available": False,
            "by_reason": {},
            "note": "not stored in analytics rows yet",
        },
        "chunk_worker": {
            "available": False,
            "saved": None,
            "failed": None,
            "note": "not stored in analytics rows yet",
        },
    }


def render_text_report(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    overall = report["overall"]
    lines = [
        "SumBot analytics report",
        (
            f"period={metadata['period']} timezone={metadata['timezone']} "
            f"model={metadata['model_filter'] or 'all'} chat={metadata['chat_filter'] or 'all'}"
        ),
        f"generated_at={metadata['generated_at']}",
        "",
        "Overall",
        (
            f"summaries={overall['summaries']} rated={overall['rated_summaries']} "
            f"coverage={_display(overall['feedback_coverage_pct'], '%')} feedback={overall['feedback']}"
        ),
        (
            f"positive={overall['positive']} ({_display(overall['positive_pct'], '%')}) "
            f"neutral={overall['neutral']} ({_display(overall['neutral_pct'], '%')}) "
            f"negative={overall['negative']} ({_display(overall['negative_pct'], '%')})"
        ),
        (
            "summary_latency="
            f"avg {_display(overall['avg_summary_duration_seconds'], 's')} / "
            f"p50 {_display(overall['p50_summary_duration_seconds'], 's')} / "
            f"p95 {_display(overall['p95_summary_duration_seconds'], 's')}"
        ),
        (
            "llm_latency="
            f"avg {_display(overall['avg_llm_duration_seconds'], 's')} / "
            f"p50 {_display(overall['p50_llm_duration_seconds'], 's')} / "
            f"p95 {_display(overall['p95_llm_duration_seconds'], 's')}"
        ),
        (
            f"tokens=avg_input {_display(overall['avg_input_tokens'])} / "
            f"avg_output {_display(overall['avg_output_tokens'])}"
        ),
        (
            "cost_estimate_usd="
            f"actual {_display(overall['estimated_cost_usd'])} / "
            f"gemma_baseline {_display(overall['gemma_baseline_cost_usd'])} / "
            f"savings {_display(overall['gemma_savings_usd'])}"
        ),
    ]

    lines.extend(("", "Top negative outliers"))
    if not report["outliers"]:
        lines.append("none")
    else:
        for item in report["outliers"]:
            lines.append(
                f"{item['dimension']}={item['key']} negative={item['negative']} "
                f"negative_pct={_display(item['negative_pct'], '%')} "
                f"coverage={_display(item['feedback_coverage_pct'], '%')} "
                f"confidence={item['confidence']}"
            )

    lines.extend(("", "Failure signals"))
    for key, item in report["failure_signals"].items():
        lines.append(f"{key}=unavailable ({item['note']})")

    for title, key in (
        ("Models", "models"),
        ("Styles", "styles"),
        ("Tones", "tones"),
        ("Aggressiveness", "aggressiveness"),
        ("Sources", "sources"),
        ("Chats", "chats"),
        ("Days", "days"),
        ("Hours", "hours"),
    ):
        lines.extend(("", title, _render_metrics_table(report[key])))

    lines.extend(("", "Negative feedback categories"))
    if not report["negative_categories"]:
        lines.append("none")
    else:
        sample_size = sum(item["count"] for item in report["negative_categories"])
        lines.append(f"sample_size={sample_size}")
        for item in report["negative_categories"]:
            lines.append(
                f"category={item['category']} count={item['count']} "
                f"pct={_display(item['pct'], '%')}"
            )

    lines.extend(("", "Latest negative details"))
    if not report["negative_details"]:
        lines.append("none")
    else:
        for item in report["negative_details"]:
            lines.append(
                f"{item['created_at']} chat={item['chat']} model={item['model']} "
                f"source={item['source']} category={item['category']}: {item['details']}"
            )
    return "\n".join(lines)


def render_csv_report(report: dict[str, Any]) -> str:
    output = io.StringIO()
    fieldnames = [
        "section",
        "key",
        "dimension",
        "summaries",
        "rated_summaries",
        "feedback",
        "feedback_coverage_pct",
        "positive",
        "neutral",
        "negative",
        "positive_pct",
        "neutral_pct",
        "negative_pct",
        "avg_summary_duration_seconds",
        "p50_summary_duration_seconds",
        "p95_summary_duration_seconds",
        "avg_llm_duration_seconds",
        "p50_llm_duration_seconds",
        "p95_llm_duration_seconds",
        "total_input_tokens",
        "total_output_tokens",
        "avg_input_tokens",
        "avg_output_tokens",
        "estimated_cost_usd",
        "gemma_baseline_cost_usd",
        "gemma_savings_usd",
        "confidence",
        "created_at",
        "chat",
        "model",
        "source",
        "category",
        "details",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerow({"section": "overall", "key": "all", **report["overall"]})
    for section in ("models", "styles", "tones", "aggressiveness", "sources", "chats", "days", "hours"):
        for row in report[section]:
            writer.writerow({"section": section, **row})
    for row in report["negative_categories"]:
        writer.writerow(
            {
                "section": "negative_categories",
                "key": row["category"],
                "negative": row["count"],
                "negative_pct": row["pct"],
            }
        )
    for row in report["negative_details"]:
        writer.writerow({"section": "negative_details", "key": row["created_at"], **row})
    for row in report["outliers"]:
        writer.writerow({"section": "outliers", **row})
    for key, row in report["failure_signals"].items():
        writer.writerow({"section": "failure_signals", "key": key, "details": row["note"]})
    return output.getvalue()


def serialize_report(report: dict[str, Any], report_format: str) -> str:
    if report_format == "text":
        return render_text_report(report)
    if report_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if report_format == "csv":
        return render_csv_report(report)
    raise ValueError(f"unsupported report format: {report_format}")


def _render_metrics_table(rows: list[dict[str, Any]]) -> str:
    headers = ("key", "sum", "rated", "cover%", "+%", "-%", "cost$", "gemma$", "save$")
    table_rows = [
        (
            row["key"],
            str(row["summaries"]),
            str(row["rated_summaries"]),
            _display(row["feedback_coverage_pct"]),
            _display(row["positive_pct"]),
            _display(row["negative_pct"]),
            _display(row["estimated_cost_usd"]),
            _display(row["gemma_baseline_cost_usd"]),
            _display(row["gemma_savings_usd"]),
        )
        for row in rows
    ]
    if not table_rows:
        return "none"

    widths = [max(len(headers[index]), *(len(row[index]) for row in table_rows)) for index in range(len(headers))]
    rendered = ["  ".join(value.ljust(widths[index]) for index, value in enumerate(headers))]
    rendered.append("  ".join("-" * width for width in widths))
    rendered.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in table_rows
    )
    return "\n".join(rendered)


def _numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _estimate_rows_cost(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    for row in rows:
        cost = _estimate_token_cost(
            str(row.get("model_name") or ""),
            int(row.get("input_tokens") or 0),
            int(row.get("output_tokens") or 0),
        )
        if cost is None:
            return None
        total += cost
    return _rounded_cost(total)


def _estimate_token_cost(model_name: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = MODEL_PRICE_USD_PER_1M_TOKENS.get(model_name.removeprefix("openrouter:"))
    if prices is None:
        return None
    input_price, output_price = prices
    return _rounded_cost((input_tokens * input_price + output_tokens * output_price) / 1_000_000)


def _percentage(value: int, total: int) -> float | None:
    return round(value * 100 / total, 2) if total else None


def _confidence_label(rated_summary_count: int, summary_count: int) -> str:
    coverage = _percentage(rated_summary_count, summary_count) or 0.0
    if rated_summary_count < LOW_CONFIDENCE_FEEDBACK_COUNT or coverage < LOW_CONFIDENCE_COVERAGE_PCT:
        return "low"
    return "ok"


def _categorize_negative_details(details: str) -> str:
    normalized = details.casefold()
    if any(
        word in normalized
        for word in (
            "factual", "wrong", "ошиб", "невер", "галлюц", "hallucin", "miss", "пропуст",
            "не упомян",
        )
    ):
        return "factual"
    if any(word in normalized for word in ("tone", "style", "стиль", "тон", "грубо", "вежливо")):
        return "tone"
    if any(
        word in normalized
        for word in ("length", "verbose", "short", "длин", "корот", "много воды")
    ):
        return "length"
    if any(word in normalized for word in ("safety", "toxic", "опасно", "цензур", "мат")):
        return "safety"
    if any(word in normalized for word in ("noise", "test", "ads", "спам", "реклам", "тест")):
        return "noise"
    return "other"


def _build_negative_categories_stats(feedback_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    negative_rows = [
        row
        for row in feedback_rows
        if row.get("sentiment") == "negative" and str(row.get("details") or "").strip()
    ]
    total_negative_with_details = len(negative_rows)

    counts: dict[str, int] = defaultdict(int)
    for row in negative_rows:
        category = _categorize_negative_details(str(row["details"]))
        counts[category] += 1

    result = []
    # Sort by count descending, then category name
    for cat in sorted(counts.keys(), key=lambda x: (-counts[x], x)):
        result.append(
            {
                "category": cat,
                "count": counts[cat],
                "pct": _percentage(counts[cat], total_negative_with_details),
            }
        )
    return result


def _average(values: list[float]) -> float | None:
    return _rounded(mean(values)) if values else None


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _rounded_cost(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _display(value: Any, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_datetime(value: datetime, report_timezone: ZoneInfo) -> datetime:
    return _as_utc(value).astimezone(report_timezone)


def _chat_title(row: dict[str, Any]) -> str:
    title = str(row.get("chat_title") or "").strip()
    return title or "Unknown chat"


def _resolve_chat_filter_label(
    summary_rows: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
    chat_id: int | None,
) -> str | None:
    if chat_id is None:
        return None
    for row in (*summary_rows, *feedback_rows):
        if row.get("chat_id") == chat_id:
            return _chat_title(row)
    return "Selected chat"


async def run_report(args: argparse.Namespace) -> int:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")

    period_delta = parse_period(args.period)
    generated_at = datetime.now(timezone.utc)
    since = generated_at - period_delta if period_delta else None
    try:
        report_timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {args.timezone}") from exc

    summary_rows, feedback_rows = await fetch_report_rows(
        db_url,
        since,
        model=args.model,
        chat_id=args.chat_id,
    )
    report = build_analytics_report(
        summary_rows,
        feedback_rows,
        period=args.period,
        report_timezone=report_timezone,
        generated_at=generated_at,
        since=since,
        model=args.model,
        chat_id=args.chat_id,
        negative_details_limit=args.negative_details_limit,
    )
    rendered = serialize_report(report, args.format)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Analytics report written to {args.output}")
    else:
        print(rendered)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an actionable SumBot analytics report.")
    parser.add_argument("--period", default="30d", help="Lookback: 24h, 7d, 4w, or all.")
    parser.add_argument("--format", choices=REPORT_FORMATS, default="text")
    parser.add_argument("--model", default=None, help="Exact model_name filter.")
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--negative-details-limit", type=int, default=10)
    parser.add_argument("--output", default=None)
    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.negative_details_limit < 0:
        parser.error("--negative-details-limit must be zero or greater")
    try:
        return asyncio.run(run_report(args))
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
