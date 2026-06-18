import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1400
HEIGHT = 2050
MARGIN = 72
BACKGROUND = "#F3F0E8"
CARD = "#FFFCF5"
INK = "#17211B"
MUTED = "#667069"
GRID = "#D9D7CF"
GREEN = "#2D7A55"
AMBER = "#D49A32"
RED = "#BD4B3E"
BLUE = "#32748F"


def render_analytics_dashboard(report: dict[str, Any]) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _font(54, bold=True),
        "subtitle": _font(25),
        "kpi": _font(43, bold=True),
        "label": _font(22, bold=True),
        "body": _font(22),
        "small": _font(18),
        "small_bold": _font(18, bold=True),
    }

    metadata = report["metadata"]
    overall = report["overall"]
    draw.text((MARGIN, 62), "SUMBOT ANALYTICS", fill=INK, font=fonts["title"])
    draw.text(
        (MARGIN, 128),
        (
            f"{metadata['period']}  |  {metadata['timezone']}  |  "
            f"model: {metadata['model_filter'] or 'all'}  |  chat: {metadata['chat_filter'] or 'all'}"
        ),
        fill=MUTED,
        font=fonts["subtitle"],
    )

    _draw_kpis(draw, fonts, overall, top=190)
    _draw_sentiment(draw, fonts, overall, top=430)
    _draw_models(draw, fonts, report["models"], top=650)
    _draw_daily_trend(draw, fonts, report["days"], top=1080)
    _draw_hourly_risk(draw, fonts, report["hours"], top=1530)

    draw.text(
        (MARGIN, HEIGHT - 52),
        "Percentages are based on saved feedback. Coverage = summaries with at least one rating.",
        fill=MUTED,
        font=fonts["small"],
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _draw_kpis(draw: ImageDraw.ImageDraw, fonts: dict[str, ImageFont.ImageFont], overall: dict, top: int) -> None:
    gap = 20
    card_width = (WIDTH - MARGIN * 2 - gap * 3) // 4
    cards = (
        ("SUMMARIES", str(overall["summaries"]), BLUE),
        ("COVERAGE", _display(overall["feedback_coverage_pct"], "%"), GREEN),
        ("POSITIVE", _display(overall["positive_pct"], "%"), GREEN),
        ("NEGATIVE", _display(overall["negative_pct"], "%"), RED),
    )
    for index, (label, value, color) in enumerate(cards):
        left = MARGIN + index * (card_width + gap)
        _card(draw, (left, top, left + card_width, top + 190))
        draw.rectangle((left, top, left + 8, top + 190), fill=color)
        draw.text((left + 28, top + 28), label, fill=MUTED, font=fonts["label"])
        draw.text((left + 28, top + 78), value, fill=INK, font=fonts["kpi"])


def _draw_sentiment(
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.ImageFont],
    overall: dict,
    top: int,
) -> None:
    _section_title(draw, fonts, "FEEDBACK STRUCTURE", top)
    bar_top = top + 62
    bar_height = 50
    bar_width = WIDTH - MARGIN * 2
    values = (
        (overall["positive"], GREEN, "Positive"),
        (overall["neutral"], AMBER, "Neutral"),
        (overall["negative"], RED, "Negative"),
    )
    total = sum(value for value, _color, _label in values)
    left = MARGIN
    for index, (value, color, _label) in enumerate(values):
        segment_width = round(bar_width * value / total) if total else 0
        if index == len(values) - 1:
            segment_width = MARGIN + bar_width - left
        draw.rectangle((left, bar_top, left + segment_width, bar_top + bar_height), fill=color)
        left += segment_width

    legend_top = bar_top + 76
    legend_gap = 380
    for index, (value, color, label) in enumerate(values):
        x = MARGIN + index * legend_gap
        draw.rounded_rectangle((x, legend_top, x + 20, legend_top + 20), radius=5, fill=color)
        percentage = _percentage(value, total)
        draw.text(
            (x + 34, legend_top - 4),
            f"{label}: {value} ({_display(percentage, '%')})",
            fill=INK,
            font=fonts["body"],
        )


def _draw_models(
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.ImageFont],
    model_rows: list[dict],
    top: int,
) -> None:
    _section_title(draw, fonts, "MODEL QUALITY", top)
    rows = sorted(model_rows, key=lambda item: item["summaries"], reverse=True)[:6]
    if not rows:
        draw.text((MARGIN, top + 70), "No model data", fill=MUTED, font=fonts["body"])
        return

    header_y = top + 64
    draw.text((MARGIN, header_y), "MODEL", fill=MUTED, font=fonts["small_bold"])
    draw.text((790, header_y), "COVERAGE", fill=MUTED, font=fonts["small_bold"])
    draw.text((990, header_y), "POSITIVE", fill=MUTED, font=fonts["small_bold"])
    draw.text((1170, header_y), "NEGATIVE", fill=MUTED, font=fonts["small_bold"])

    for index, row in enumerate(rows):
        y = header_y + 50 + index * 56
        if index % 2 == 0:
            draw.rounded_rectangle((MARGIN, y - 10, WIDTH - MARGIN, y + 42), radius=10, fill=CARD)
        draw.text((MARGIN + 14, y), _truncate(str(row["key"]), 42), fill=INK, font=fonts["body"])
        draw.text((790, y), _display(row["feedback_coverage_pct"], "%"), fill=BLUE, font=fonts["body"])
        draw.text((990, y), _display(row["positive_pct"], "%"), fill=GREEN, font=fonts["body"])
        draw.text((1170, y), _display(row["negative_pct"], "%"), fill=RED, font=fonts["body"])


def _draw_daily_trend(
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.ImageFont],
    day_rows: list[dict],
    top: int,
) -> None:
    _section_title(draw, fonts, "DAILY VOLUME AND NEGATIVE RATE", top)
    rows = day_rows[-14:]
    if not rows:
        draw.text((MARGIN, top + 70), "No daily data", fill=MUTED, font=fonts["body"])
        return

    chart_left = MARGIN
    chart_top = top + 72
    chart_width = WIDTH - MARGIN * 2
    chart_height = 270
    baseline = chart_top + chart_height
    max_summaries = max(row["summaries"] for row in rows) or 1
    slot_width = chart_width / len(rows)
    draw.line((chart_left, baseline, chart_left + chart_width, baseline), fill=GRID, width=2)

    points = []
    for index, row in enumerate(rows):
        center_x = chart_left + slot_width * index + slot_width / 2
        bar_height = chart_height * 0.72 * row["summaries"] / max_summaries
        draw.rounded_rectangle(
            (center_x - slot_width * 0.27, baseline - bar_height, center_x + slot_width * 0.27, baseline),
            radius=8,
            fill=BLUE,
        )
        negative_pct = row["negative_pct"] or 0
        point_y = baseline - chart_height * negative_pct / 100
        points.append((center_x, point_y))
        draw.text(
            (center_x - 28, baseline + 14),
            str(row["key"])[5:],
            fill=MUTED,
            font=fonts["small"],
        )
    if len(points) > 1:
        draw.line(points, fill=RED, width=5, joint="curve")
    for point in points:
        draw.ellipse((point[0] - 7, point[1] - 7, point[0] + 7, point[1] + 7), fill=RED)
    draw.text((MARGIN, baseline + 55), "Blue bars: summaries", fill=BLUE, font=fonts["small_bold"])
    draw.text((MARGIN + 250, baseline + 55), "Red line: negative %", fill=RED, font=fonts["small_bold"])


def _draw_hourly_risk(
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.ImageFont],
    hour_rows: list[dict],
    top: int,
) -> None:
    _section_title(draw, fonts, "HOURS WITH THE MOST NEGATIVE FEEDBACK", top)
    rows = sorted(
        (row for row in hour_rows if row["negative_pct"] is not None),
        key=lambda item: (item["negative_pct"], item["feedback"]),
        reverse=True,
    )[:6]
    if not rows:
        draw.text((MARGIN, top + 70), "No rated hourly data", fill=MUTED, font=fonts["body"])
        return

    chart_top = top + 72
    max_width = WIDTH - MARGIN * 2 - 220
    for index, row in enumerate(rows):
        y = chart_top + index * 55
        draw.text((MARGIN, y), str(row["key"]), fill=INK, font=fonts["body"])
        bar_left = MARGIN + 100
        bar_width = max_width * row["negative_pct"] / 100
        draw.rounded_rectangle((bar_left, y + 2, bar_left + max_width, y + 28), radius=10, fill=GRID)
        draw.rounded_rectangle((bar_left, y + 2, bar_left + bar_width, y + 28), radius=10, fill=RED)
        draw.text(
            (bar_left + max_width + 24, y),
            f"{row['negative_pct']}%",
            fill=RED,
            font=fonts["small_bold"],
        )


def _section_title(draw: ImageDraw.ImageDraw, fonts: dict[str, ImageFont.ImageFont], text: str, top: int) -> None:
    draw.text((MARGIN, top), text, fill=INK, font=fonts["label"])
    draw.line((MARGIN, top + 40, WIDTH - MARGIN, top + 40), fill=GRID, width=2)


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=24, fill=CARD, outline=GRID, width=2)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


def _display(value: object, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def _percentage(value: int, total: int) -> float | None:
    return round(value * 100 / total, 2) if total else None


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}..."
