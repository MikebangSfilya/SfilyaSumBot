from __future__ import annotations

import argparse
import gc
import sys
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from anonymizer import Anonymizer
from sumbot.summary_context import SummaryMessage


DEFAULT_SIZES = (100, 1_000, 5_000)
DEFAULT_REPEATS = 7
DEFAULT_AUTHORS = 50


@dataclass(frozen=True)
class BenchResult:
    case: str
    items: int
    chars: int
    median_ms: float
    min_ms: float
    max_ms: float

    @property
    def chars_per_second(self) -> float:
        if self.median_ms <= 0:
            return 0.0
        return self.chars / (self.median_ms / 1_000)


def parse_sizes(raw: str) -> tuple[int, ...]:
    sizes = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise argparse.ArgumentTypeError("sizes must be positive integers, e.g. 100,1000,5000")
    return sizes


def build_message_body(index: int, authors: int) -> str:
    target = (index + 7) % authors
    variants = (
        f"проверь alice{index}@example.com и +7 999 {index % 900 + 100:03d} 45 67",
        f"ссылка https://example.com/tasks/{index}?owner=user{target} нужна @user{target}",
        f"короткий апдейт без pii, но с упоминанием @ghost_{index % 17}",
        f"backup email bob-{index}@sample.org, phone 89991234567, url http://test.local/{index}",
    )
    return variants[index % len(variants)]


def build_structured_messages(size: int, authors: int) -> list[SummaryMessage]:
    messages: list[SummaryMessage] = []
    for index in range(size):
        author_id = index % authors
        reply_id = (index - 1) % authors if index % 5 == 0 else None
        messages.append(
            SummaryMessage(
                ts=float(index),
                author_name=f"Author {author_id}",
                author_id=author_id,
                author_username=f"user{author_id}",
                reply_to_name=f"Author {reply_id}" if reply_id is not None else None,
                reply_to_user_id=reply_id,
                message_text=build_message_body(index, authors),
            )
        )
    return messages


def build_legacy_items(size: int, authors: int) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index in range(size):
        author_id = index % authors
        items.append(
            {
                "ts": float(index),
                "text": (
                    f"[01.01 10:{index % 60:02d}] Author {author_id} (@user{author_id}): "
                    f"{build_message_body(index, authors)}"
                ),
            }
        )
    return items


def build_decode_text(authors: int, repeats: int) -> tuple[Anonymizer, str]:
    anonymizer = Anonymizer()
    for author_id in range(authors):
        anonymizer._get_fake_name(f"Author {author_id}")
    tokens = [f"User_{author_id % authors + 1}" for author_id in range(repeats)]
    return anonymizer, " ".join(tokens)


def time_case(
    case: str,
    items: int,
    chars: int,
    repeats: int,
    func: Callable[[], object],
) -> BenchResult:
    timings: list[float] = []

    func()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            func()
            timings.append((time.perf_counter() - started) * 1_000)
    finally:
        if was_enabled:
            gc.enable()

    return BenchResult(
        case=case,
        items=items,
        chars=chars,
        median_ms=statistics.median(timings),
        min_ms=min(timings),
        max_ms=max(timings),
    )


def benchmark_size(size: int, authors: int, repeats: int) -> Iterable[BenchResult]:
    structured_messages = build_structured_messages(size, authors)
    legacy_items = build_legacy_items(size, authors)
    structured_chars = sum(len(message.message_text) for message in structured_messages)
    legacy_chars = sum(len(str(item["text"])) for item in legacy_items)

    yield time_case(
        "render_messages_for_llm",
        size,
        structured_chars,
        repeats,
        lambda: Anonymizer().render_messages_for_llm(structured_messages),
    )
    yield time_case(
        "clean_text_for_llm",
        size,
        legacy_chars,
        repeats,
        lambda: Anonymizer().clean_text_for_llm(legacy_items),
    )

    decode_anonymizer, decode_text = build_decode_text(authors, size)
    yield time_case(
        "decode",
        size,
        len(decode_text),
        repeats,
        lambda: decode_anonymizer.decode(decode_text),
    )


def print_results(results: list[BenchResult]) -> None:
    header = (
        f"{'case':<24} {'items':>8} {'chars':>10} {'median ms':>12} "
        f"{'min ms':>10} {'max ms':>10} {'chars/s':>14}"
    )
    print(header)
    print("-" * len(header))
    for result in results:
        print(
            f"{result.case:<24} {result.items:>8} {result.chars:>10} "
            f"{result.median_ms:>12.3f} {result.min_ms:>10.3f} {result.max_ms:>10.3f} "
            f"{result.chars_per_second:>14,.0f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark SumBot anonymizer hot paths.")
    parser.add_argument(
        "--sizes",
        type=parse_sizes,
        default=DEFAULT_SIZES,
        help="Comma-separated message counts, e.g. 100,1000,5000.",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="Measured repeats per case.")
    parser.add_argument("--authors", type=int, default=DEFAULT_AUTHORS, help="Distinct synthetic authors.")
    args = parser.parse_args()

    if args.repeats <= 0:
        parser.error("--repeats must be positive")
    if args.authors <= 0:
        parser.error("--authors must be positive")

    results: list[BenchResult] = []
    for size in args.sizes:
        results.extend(benchmark_size(size, args.authors, args.repeats))
    print_results(results)


if __name__ == "__main__":
    main()
