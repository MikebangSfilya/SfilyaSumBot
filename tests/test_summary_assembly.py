from anonymizer import Anonymizer
from sumbot.chunks import ChunkEvent, ChunkParticipant, ChunkSummaryRecord
from sumbot.summary_assembly import (
    SummarySourceBundle,
    build_chunk_native_prepared_context,
    render_chunk_records_for_llm,
    summarize_source_bundle_stats,
)


def make_chunk_record() -> ChunkSummaryRecord:
    return ChunkSummaryRecord(
        chat_id=42,
        chunk_id="42-1",
        message_count=50,
        ts_from=100.0,
        ts_to=200.0,
        participants=(
            ChunkParticipant(
                speaker_ref="speaker_id_10",
                author_name="Alice",
                author_id=10,
                author_username="alice",
            ),
            ChunkParticipant(
                speaker_ref="speaker_id_20",
                author_name="Bob",
                author_id=20,
                author_username="bob",
            ),
        ),
        topics=("сервер", "деплой"),
        events=(
            ChunkEvent(speaker_ref="speaker_id_10", text="упал сервер"),
            ChunkEvent(speaker_ref="speaker_id_20", reply_to_ref="speaker_id_10", text="предложил рестарт @alice"),
        ),
        open_loops=("неясно, кто чинит",),
        source_message_count=50,
    )


def test_render_chunk_records_for_llm_keeps_structure_and_masks_mentions():
    anonymizer = Anonymizer()
    rendered, turn_count = render_chunk_records_for_llm([make_chunk_record()], anonymizer)

    assert "[Chunk 1 | сообщений: 50" in rendered
    assert "Темы: сервер; деплой" in rendered
    assert "User_1: упал сервер" in rendered
    assert "User_2 (в ответ User_1): предложил рестарт @User_1" in rendered
    assert "Подвешенные хвосты: неясно, кто чинит" in rendered
    assert turn_count == 2


def test_build_chunk_native_prepared_context_combines_chunks_with_live_tail():
    bundle = SummarySourceBundle(
        raw_messages=[
            {
                "ts": 300.0,
                "author_name": "Alice",
                "author_id": 10,
                "author_username": "alice",
                "message_text": "свежий хвост",
            }
        ],
        chunk_records=[make_chunk_record()],
    )

    prepared_context, anonymizer = build_chunk_native_prepared_context(bundle, enable_context_v2=True)

    assert "<precomputed_chunk_summaries>" in prepared_context.rendered_text
    assert "<live_chat_tail>" in prepared_context.rendered_text
    assert "User_1: упал сервер" in prepared_context.rendered_text
    assert "User_1" in prepared_context.rendered_text
    assert anonymizer.decode("User_1 и User_2") == "Alice и Bob"
    assert prepared_context.raw_message_count == 51
    assert prepared_context.turn_count >= 3


def test_summarize_source_bundle_stats_counts_chunk_and_raw_parts():
    bundle = SummarySourceBundle(
        raw_messages=[
            {
                "ts": 300.0,
                "author_name": "Alice",
                "author_id": 10,
                "author_username": "alice",
                "message_text": "свежий хвост",
            }
        ],
        chunk_records=[make_chunk_record()],
    )

    stats = summarize_source_bundle_stats(bundle)

    assert stats["unique_authors"] == 2
    assert stats["replies"] == 1
    assert stats["text_chars"] > 0
    assert stats["timespan_seconds"] == 200
