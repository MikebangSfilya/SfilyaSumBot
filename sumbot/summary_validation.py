from __future__ import annotations

import re
from dataclasses import dataclass


POLITICAL_TOPIC_PATTERN = re.compile(
    r"\b(?:политик\w*|выбор\w*|президент\w*|правительств\w*|государств\w*|"
    r"путин\w*|зеленск\w*|трамп\w*|байден\w*|росси\w*|украин\w*|сша|нато|"
    r"израил\w*|палестин\w*|хамас\w*|арм(?:ия|ии|ию|ией)|всу|военн\w*|войн\w*|"
    r"конфликт\w*|диктатор\w*|режим\w*)\b",
    flags=re.IGNORECASE,
)
POLITICAL_STANCE_PATTERN = re.compile(
    r"\b(?:правильн\w*|справедлив\w*|героич\w*|позорн\w*|заслуженн\w*|"
    r"база|кринж|имба|молодц\w*|прав(?:а|ы)?|виноват\w*|поддерж\w*|осужд\w*)\b",
    flags=re.IGNORECASE,
)
POLITICAL_REFERENCE_PATTERN = re.compile(
    r"\b(?:эта|эту|этой|одна|одну|одной)\s+сторон\w*|\b(?:их|его|ее|её)\s+(?:действи\w*|решени\w*)",
    flags=re.IGNORECASE,
)
FIRST_PERSON_STANCE_PATTERN = re.compile(
    r"\b(?:я|мы)\s+(?:поддержива\w*|одобря\w*|осужда\w*|за\s+|против\s+)",
    flags=re.IGNORECASE,
)
POLITICAL_CALL_TO_ACTION_PATTERN = re.compile(
    r"\b(?:надо|нужно|следует|необходимо|должны?)\s+"
    r"(?:поддерж\w*|осуд\w*|наказать\w*|уничтож\w*|побед\w*|сверг\w*)",
    flags=re.IGNORECASE,
)
ATTRIBUTED_OPINION_PATTERN = re.compile(
    r"(?:\bUser_\d+\b.{0,80}\b(?:сказал\w*|написал\w*|заявил\w*|считает\w*|"
    r"назвал\w*|утвержда\w*|отметил\w*|предположил\w*|поддержал\w*|осудил\w*)\b|"
    r"\b(?:по мнению|со слов)\s+User_\d+\b)",
    flags=re.IGNORECASE,
)
ROLE_TAG_PATTERN = re.compile(
    r"User_\d+\s*\[(?:[а-яё -]+|в ответ User_\d+|отвечает User_\d+)\]",
    flags=re.IGNORECASE,
)
HTML_TAG_PATTERN = re.compile(r"</?[a-z][^>]*>", flags=re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\([^\)]+\)")
MARKDOWN_LIST_PATTERN = re.compile(r"(?m)^\s*(?:[-+]\s+|\d+[.)]\s+)")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class SummaryValidationResult:
    is_valid: bool
    reasons: tuple[str, ...] = ()


def validate_summary_output(summary_text: str, source_text: str) -> SummaryValidationResult:
    reasons = list(_find_format_violations(summary_text))
    if POLITICAL_TOPIC_PATTERN.search(source_text) and _contains_unattributed_political_stance(summary_text):
        reasons.append("political_stance")
    return SummaryValidationResult(is_valid=not reasons, reasons=tuple(dict.fromkeys(reasons)))


def build_validation_retry_instruction(reasons: tuple[str, ...]) -> str:
    reason_labels = {
        "markup": "служебная или Markdown-разметка",
        "role_tag": "служебные role-tags возле User_N",
        "political_stance": "неприписанная участнику оценка политической стороны или призыв",
    }
    details = ", ".join(reason_labels.get(reason, reason) for reason in reasons)
    return (
        "[VALIDATION RETRY]: Предыдущий ответ отклонен автоматической проверкой: "
        f"{details}. Сгенерируй полный пересказ заново. Верни только чистый русский текст без Markdown, "
        "HTML и служебных тегов. Политические оценки передавай только как явно приписанные слова участников; "
        "сам не поддерживай и не осуждай стороны и не добавляй призывы."
    )


def _find_format_violations(summary_text: str) -> tuple[str, ...]:
    reasons: list[str] = []
    text_without_user_ids = re.sub(r"User_\d+", "User", summary_text)
    if (
        any(symbol in summary_text for symbol in ("*", "#", "`"))
        or "_" in text_without_user_ids
        or HTML_TAG_PATTERN.search(summary_text)
        or MARKDOWN_LINK_PATTERN.search(summary_text)
        or MARKDOWN_LIST_PATTERN.search(summary_text)
    ):
        reasons.append("markup")
    if ROLE_TAG_PATTERN.search(summary_text):
        reasons.append("role_tag")
    return tuple(reasons)


def _contains_unattributed_political_stance(summary_text: str) -> bool:
    for sentence in SENTENCE_SPLIT_PATTERN.split(summary_text):
        sentence = sentence.strip()
        if not sentence:
            continue
        discusses_politics = POLITICAL_TOPIC_PATTERN.search(sentence) or POLITICAL_REFERENCE_PATTERN.search(sentence)
        if not discusses_politics or ATTRIBUTED_OPINION_PATTERN.search(sentence):
            continue
        if (
            FIRST_PERSON_STANCE_PATTERN.search(sentence)
            or POLITICAL_CALL_TO_ACTION_PATTERN.search(sentence)
            or POLITICAL_STANCE_PATTERN.search(sentence)
        ):
            return True
    return False
