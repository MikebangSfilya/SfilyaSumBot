from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anonymizer import Anonymizer
from sumbot.chunks import ChunkSummaryRecord, fetch_chunk_summary_records
from sumbot.summary_context import PreparedSummaryContext, prepare_summary_context


@dataclass(frozen=True, slots=True)
class SummarySourceBundle:
    raw_messages: list[dict[str, Any]]
    chunk_records: list[ChunkSummaryRecord]

    @property
    def is_chunk_native(self) -> bool:
        return bool(self.chunk_records)

    @property
    def total_source_messages(self) -> int:
        return len(self.raw_messages) + sum(record.source_message_count for record in self.chunk_records)


async def fetch_summary_source_bundle(
    redis,
    chat_id: int,
    *,
    raw_messages: list[dict[str, Any]],
    limit_messages: int | None,
    time_limit_seconds: int | None,
    chunking_enabled: bool,
    current_ts: float,
) -> SummarySourceBundle:
    if not chunking_enabled:
        return SummarySourceBundle(raw_messages=raw_messages, chunk_records=[])

    chunk_records = await select_chunk_summary_records(
        redis,
        chat_id,
        raw_messages=raw_messages,
        limit_messages=limit_messages,
        time_limit_seconds=time_limit_seconds,
        current_ts=current_ts,
    )
    return SummarySourceBundle(raw_messages=raw_messages, chunk_records=chunk_records)


async def select_chunk_summary_records(
    redis,
    chat_id: int,
    *,
    raw_messages: list[dict[str, Any]],
    limit_messages: int | None,
    time_limit_seconds: int | None,
    current_ts: float,
) -> list[ChunkSummaryRecord]:
    summaries = await fetch_chunk_summary_records(redis, chat_id)
    if not summaries:
        return []

    raw_oldest_ts = _get_oldest_ts(raw_messages)
    selected: list[ChunkSummaryRecord] = []
    if limit_messages is not None:
        covered_messages = len(raw_messages)
        if covered_messages >= limit_messages:
            return []
        for record in reversed(summaries):
            if raw_oldest_ts is not None and record.ts_to >= raw_oldest_ts:
                continue
            selected.append(record)
            covered_messages += max(record.source_message_count, record.message_count, 0)
            if covered_messages >= limit_messages:
                break
        selected.reverse()
        return selected

    cutoff_ts = current_ts - time_limit_seconds if time_limit_seconds is not None else 0.0
    for record in summaries:
        if record.ts_to < cutoff_ts:
            continue
        if raw_oldest_ts is not None and record.ts_to >= raw_oldest_ts:
            continue
        selected.append(record)
    return selected


def build_chunk_native_prepared_context(
    bundle: SummarySourceBundle,
    *,
    enable_context_v2: bool,
) -> tuple[PreparedSummaryContext, Anonymizer]:
    anonymizer = Anonymizer()
    chunk_text, chunk_turns = render_chunk_records_for_llm(bundle.chunk_records, anonymizer)

    raw_context = prepare_summary_context(bundle.raw_messages, anonymizer, enable_v2=enable_context_v2)

    sections: list[str] = []
    if chunk_text:
        sections.append(
            "Старый контекст уже ужат в готовые factual chunk summaries. "
            "Это не сырой лог, а подготовленная выжимка прошлых сообщений.\n"
            "<precomputed_chunk_summaries>\n"
            f"{chunk_text}\n"
            "</precomputed_chunk_summaries>"
        )
    if raw_context.rendered_text:
        sections.append(
            "Это свежий сырой хвост чата, его приоритет выше по новизне.\n"
            "<live_chat_tail>\n"
            f"{raw_context.rendered_text}\n"
            "</live_chat_tail>"
        )

    effective_turn_count = max(chunk_turns + raw_context.turn_count, min(bundle.total_source_messages, 50))
    return (
        PreparedSummaryContext(
            rendered_text="\n\n".join(section for section in sections if section),
            raw_message_count=bundle.total_source_messages,
            turn_count=effective_turn_count,
            merged_count=raw_context.merged_count,
        ),
        anonymizer,
    )


def render_chunk_records_for_llm(
    chunk_records: list[ChunkSummaryRecord],
    anonymizer: Anonymizer,
) -> tuple[str, int]:
    if not chunk_records:
        return "", 0

    speaker_aliases: dict[str, str] = {}
    username_to_fake: dict[str, str] = {}
    for record in chunk_records:
        for participant in record.participants:
            fake_name = anonymizer.get_or_create_fake_user(
                participant.author_name,
                user_id=participant.author_id,
                username=participant.author_username,
            )
            speaker_aliases[participant.speaker_ref] = fake_name
            if participant.author_username:
                username_to_fake[participant.author_username.lower()] = fake_name

    rendered_chunks: list[str] = []
    total_events = 0
    for index, record in enumerate(chunk_records, start=1):
        lines = [
            f"[Chunk {index} | сообщений: {record.source_message_count} | период: {int(record.ts_from)}-{int(record.ts_to)}]"
        ]
        if record.topics:
            lines.append("Темы: " + "; ".join(record.topics))
        lines.append("События:")
        for event in record.events:
            speaker_fake = speaker_aliases.get(event.speaker_ref)
            if not speaker_fake:
                continue
            reply_fake = speaker_aliases.get(event.reply_to_ref or "")
            event_text = anonymizer.mask_text_for_llm(event.text, username_to_fake)
            if reply_fake:
                lines.append(f"{speaker_fake} (в ответ {reply_fake}): {event_text}")
            else:
                lines.append(f"{speaker_fake}: {event_text}")
            total_events += 1
        if record.open_loops:
            lines.append("Подвешенные хвосты: " + "; ".join(record.open_loops))
        rendered_chunks.append("\n".join(lines))

    return "\n\n".join(rendered_chunks), total_events


def summarize_source_bundle_stats(bundle: SummarySourceBundle) -> dict[str, int]:
    timestamps: list[float] = []
    author_keys: set[str] = set()
    text_chars = 0
    replies = 0

    for item in bundle.raw_messages:
        text = item.get("message_text")
        if not isinstance(text, str):
            text = item.get("text")
        if isinstance(text, str):
            text_chars += len(text)

        ts = item.get("ts")
        if isinstance(ts, int | float):
            timestamps.append(float(ts))

        author_id = item.get("author_id")
        author_username = item.get("author_username")
        author_name = item.get("author_name")
        if isinstance(author_id, int):
            author_keys.add(f"id:{author_id}")
        elif isinstance(author_username, str) and author_username:
            author_keys.add(f"username:{author_username}")
        elif isinstance(author_name, str) and author_name:
            author_keys.add(f"name:{author_name}")

        if item.get("reply_to_user_id") or item.get("reply_to_username") or item.get("reply_to_name"):
            replies += 1

    for record in bundle.chunk_records:
        timestamps.extend([record.ts_from, record.ts_to])
        text_chars += sum(len(topic) for topic in record.topics)
        text_chars += sum(len(event.text) for event in record.events)
        text_chars += sum(len(item) for item in record.open_loops)
        replies += sum(1 for event in record.events if event.reply_to_ref)
        for participant in record.participants:
            if participant.author_id is not None:
                author_keys.add(f"id:{participant.author_id}")
            elif participant.author_username:
                author_keys.add(f"username:{participant.author_username}")
            else:
                author_keys.add(f"name:{participant.author_name}")

    timespan_seconds = int(max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0
    return {
        "text_chars": text_chars,
        "unique_authors": len(author_keys),
        "replies": replies,
        "timespan_seconds": max(timespan_seconds, 0),
    }


def _get_oldest_ts(messages: list[dict[str, Any]]) -> float | None:
    timestamps = [float(item["ts"]) for item in messages if isinstance(item.get("ts"), int | float)]
    if not timestamps:
        return None
    return min(timestamps)
