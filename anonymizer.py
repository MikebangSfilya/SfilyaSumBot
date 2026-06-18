import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SumBot")


@dataclass(frozen=True, slots=True)
class StructuredMessage:
    author_name: str
    message_text: str
    author_id: int | None = None
    author_username: str | None = None
    reply_to_user_id: int | None = None
    reply_to_username: str | None = None
    reply_to_name: str | None = None


class Anonymizer:
    """
    Класс для анонимизации персональных данных в логах чата.
    
    Заменяет реальные имена и ники на псевдонимы User_1, User_2 и т.д.,
    маскирует email, телефоны и ссылки для защиты PII.
    Использует регулярные выражения, устойчивые к спецсимволам и эмодзи.
    """
    
    # Регулярное выражение для разбора заголовка сообщения
    # Формат: [ДД.ММ ЧЧ:ММ] Имя (возможно с эмодзи и скобками) (@username?) (в ответ Имя?): текст
    # Более гибкое: захватываем всё до первого "(@" или " (в ответ" или ":"
    HEADER_PATTERN = re.compile(
        r'^\[(?P<date>\d{2}\.\d{2}\s\d{2}:\d{2})\]\s*'
        r'(?P<name>(?:[^(@]|\([^)]*\))*?)'  # имя может содержать скобки, но не начинаться с (@
        r'(?:\s*\(@(?P<username>[^)]+)\))?'  # опциональный username
        r'(?:\s*\(в ответ\s+(?P<reply_to>[^)]+)\))?'  # опциональный reply
        r'\s*:\s*'
        r'(?P<message>.*)$'
    )
    
    # Паттерны для PII данных в теле сообщения
    EMAIL_PATTERN = re.compile(
        r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
    )
    
    # Российские и международные номера телефонов
    # Захватывает знак + и весь номер для полной замены
    PHONE_PATTERN = re.compile(
        r'(?:\+7|8|7)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}\b|'
        r'\b\d{3}[\s\-.]?\d{3}[\s\-.]?\d{4}\b'
    )
    
    # URL (http/https)
    URL_PATTERN = re.compile(
        r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/[-\w._~:/?#[\]@!$&\'()*+,;%=]*)?'
    )
    
    # Упоминания через @ (поддерживает Unicode, дефисы, подчеркивания)
    MENTION_PATTERN = re.compile(r'@([\w\u0400-\u04FF\u0500-\u052F\-_.]+)')
    
    def __init__(self) -> None:
        """Инициализация анонимизатора."""
        self.real_to_fake: Dict[str, str] = {}
        self.fake_to_real: Dict[str, str] = {}
        self.counter: int = 1
        self._seen_names: set = set()
        self.mention_to_fake: Dict[str, str] = {}
        self.mention_counter: int = 1
        self.name_to_fake: Dict[str, str] = {}
    
    def _get_fake_name(self, real_name: str) -> str:
        """
        Возвращает псевдоним для реального имени.
        Если имя встречается впервые, создает новый псевдоним.
        
        Args:
            real_name: Реальное имя пользователя
            
        Returns:
            Псевдоним вида "User_N"
        """
        if real_name not in self.real_to_fake:
            fake_name = f"User_{self.counter}"
            self.real_to_fake[real_name] = fake_name
            self.fake_to_real[fake_name] = real_name
            self.counter += 1
            self._seen_names.add(real_name)
            self.name_to_fake.setdefault(real_name, fake_name)
        return self.real_to_fake[real_name]
    
    def _extract_user_info(self, header: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Извлекает информацию о пользователе из заголовка сообщения.
        
        Args:
            header: Заголовок сообщения в формате лога
            
        Returns:
            Кортеж (имя, username, reply_to) или (None, None, None) при неудаче
        """
        match = self.HEADER_PATTERN.match(header)
        if not match:
            # Fallback: попробуем более простой парсинг
            return self._fallback_parse(header)
        
        name = match.group('name').strip()
        username = match.group('username')
        reply_to = match.group('reply_to')
        
        # Очищаем имя от возможных остаточных скобок
        if name.endswith(')'):
            # Если в имени были скобки, но паттерн их не отделил
            name = name.rsplit('(', 1)[0].strip()
        
        return name, username, reply_to
    
    def _fallback_parse(self, header: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Резервный метод разбора заголовка, если основной паттерн не сработал.
        Использует более простую логику.
        """
        # Убираем временную метку
        if not header.startswith('['):
            return None, None, None
        
        # Находим конец временной метки
        end_bracket = header.find(']')
        if end_bracket == -1:
            return None, None, None
        
        rest = header[end_bracket + 1:].strip()
        
        # Ищем двоеточие, разделяющее заголовок и сообщение
        colon_pos = rest.find(':')
        if colon_pos == -1:
            return None, None, None
        
        user_part = rest[:colon_pos].strip()
        # Оставляем разбор user_part для имени, username, reply_to
        # Простейший вариант: берем всё до первого '(' или оставляем как есть
        name = user_part
        username = None
        reply_to = None
        
        # Пытаемся вытащить username в скобках
        username_match = re.search(r'\(@([^)]+)\)', user_part)
        if username_match:
            username = username_match.group(1)
            name = re.sub(r'\s*\(@[^)]+\)', '', name)
        
        # Пытаемся вытащить reply_to
        reply_match = re.search(r'\(в ответ\s+([^)]+)\)', user_part)
        if reply_match:
            reply_to = reply_match.group(1)
            name = re.sub(r'\s*\(в ответ\s+[^)]+\)', '', name)
        
        name = name.strip()
        return name, username, reply_to
    
    def _mask_pii_in_text(self, text: str, username_to_fake: Mapping[str, str] | None = None) -> str:
        """
        Маскирует PII данные в тексте сообщения.
        
        Args:
            text: Исходный текст сообщения
            
        Returns:
            Текст с замененными email, телефонами и ссылками
        """
        # Заменяем email на [EMAIL]
        text = self.EMAIL_PATTERN.sub('[EMAIL]', text)
        
        # Заменяем телефоны на [PHONE]
        text = self.PHONE_PATTERN.sub('[PHONE]', text)
        
        # Заменяем URL на [URL] (опционально, для экономии токенов)
        text = self.URL_PATTERN.sub('[URL]', text)
        
        text = self._mask_mentions(text, username_to_fake)

        return text

    def mask_text_for_llm(self, text: str, username_to_fake: Mapping[str, str] | None = None) -> str:
        return self._mask_pii_in_text(text, username_to_fake)

    def _mask_mentions(self, text: str, username_to_fake: Mapping[str, str] | None = None) -> str:
        username_to_fake = username_to_fake or {}

        def replace(match: re.Match[str]) -> str:
            mention = match.group(1)
            normalized = mention.lower()
            if normalized in username_to_fake:
                return f"@{username_to_fake[normalized]}"
            if normalized not in self.mention_to_fake:
                self.mention_to_fake[normalized] = f"@mention_{self.mention_counter}"
                self.mention_counter += 1
            return self.mention_to_fake[normalized]

        return self.MENTION_PATTERN.sub(replace, text)

    @staticmethod
    def _should_skip_name(name: str) -> bool:
        normalized_name = name.strip().lower()
        return normalized_name == "bot" or "bot" in normalized_name

    @staticmethod
    def _extract_structured_message(item: Any) -> StructuredMessage | None:
        author_name = getattr(item, "author_name", None)
        message_text = getattr(item, "message_text", None)

        if isinstance(author_name, str) and isinstance(message_text, str):
            return StructuredMessage(
                author_name=author_name.strip(),
                message_text=message_text.strip(),
                author_id=getattr(item, "author_id", None),
                author_username=getattr(item, "author_username", None),
                reply_to_user_id=getattr(item, "reply_to_user_id", None),
                reply_to_username=getattr(item, "reply_to_username", None),
                reply_to_name=getattr(item, "reply_to_name", None),
            )

        if not isinstance(item, dict):
            return None

        author_name = item.get("author_name")
        message_text = item.get("message_text")
        if isinstance(author_name, str) and isinstance(message_text, str):
            return StructuredMessage(
                author_name=author_name.strip(),
                message_text=message_text.strip(),
                author_id=item.get("author_id"),
                author_username=item.get("author_username"),
                reply_to_user_id=item.get("reply_to_user_id"),
                reply_to_username=item.get("reply_to_username"),
                reply_to_name=item.get("reply_to_name"),
            )
        return None

    @staticmethod
    def _format_speaker(fake_name: str, role_tag: str | None, reply_fake: str | None) -> str:
        if not role_tag:
            if reply_fake:
                return f"{fake_name} [в ответ {reply_fake}]"
            return fake_name
        if role_tag == "отвечает" and reply_fake:
            return f"{fake_name} [отвечает {reply_fake}]"
        return f"{fake_name} [{role_tag}]"

    @staticmethod
    def _identity_key(name: str, user_id: int | None = None, username: str | None = None) -> str:
        if isinstance(user_id, int):
            return f"id:{user_id}"
        if isinstance(username, str) and username.strip():
            return f"username:{username.strip().lower()}"
        return f"name:{name.strip()}"

    def _get_fake_user(
        self,
        name: str,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> str:
        identity_key = self._identity_key(name, user_id=user_id, username=username)
        if identity_key not in self.real_to_fake:
            fake_name = f"User_{self.counter}"
            self.real_to_fake[identity_key] = fake_name
            self.fake_to_real[fake_name] = name
            self.counter += 1
            self._seen_names.add(identity_key)

        fake_name = self.real_to_fake[identity_key]
        self.name_to_fake.setdefault(name, fake_name)
        return fake_name

    def get_or_create_fake_user(
        self,
        name: str,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> str:
        return self._get_fake_user(name, user_id=user_id, username=username)

    @staticmethod
    def _normalize_optional_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def _normalize_optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_structured_message(self, message: StructuredMessage) -> StructuredMessage:
        return StructuredMessage(
            author_name=message.author_name.strip(),
            message_text=message.message_text.strip(),
            author_id=self._normalize_optional_int(message.author_id),
            author_username=self._normalize_optional_str(message.author_username),
            reply_to_user_id=self._normalize_optional_int(message.reply_to_user_id),
            reply_to_username=self._normalize_optional_str(message.reply_to_username),
            reply_to_name=self._normalize_optional_str(message.reply_to_name),
        )

    def _resolve_reply_fake(self, message: StructuredMessage) -> str | None:
        if not message.reply_to_name:
            return None
        reply_key = self._identity_key(
            message.reply_to_name,
            user_id=message.reply_to_user_id,
            username=message.reply_to_username,
        )
        if reply_key in self.real_to_fake:
            return self.real_to_fake[reply_key]
        if message.reply_to_name in self.name_to_fake:
            return self.name_to_fake[message.reply_to_name]
        return self._get_fake_user(
            message.reply_to_name,
            user_id=message.reply_to_user_id,
            username=message.reply_to_username,
        )

    def render_messages_for_llm(
        self,
        messages: Sequence[Any],
        *,
        role_tags: Mapping[str, str] | None = None,
    ) -> str:
        processed: List[str] = []
        structured_messages: List[StructuredMessage] = []

        for item in messages:
            message = self._extract_structured_message(item)
            if message is None:
                logger.debug("Skipping malformed structured message: %r", item)
                continue
            message = self._normalize_structured_message(message)
            if not message.author_name or message.message_text is None:
                logger.debug("Skipping malformed structured message: %r", item)
                continue
            if self._should_skip_name(message.author_name):
                continue

            self._get_fake_user(
                message.author_name,
                user_id=message.author_id,
                username=message.author_username,
            )
            structured_messages.append(message)

        username_to_fake = {
            message.author_username.lower(): self._get_fake_user(
                message.author_name,
                user_id=message.author_id,
                username=message.author_username,
            )
            for message in structured_messages
            if message.author_username
        }

        for message in structured_messages:
            try:
                fake_name = self._get_fake_user(
                    message.author_name,
                    user_id=message.author_id,
                    username=message.author_username,
                )
                reply_fake = self._resolve_reply_fake(message)
                clean_body = self._mask_pii_in_text(message.message_text, username_to_fake)
                role_tag = None
                if role_tags:
                    role_tag = role_tags.get(
                        self._identity_key(message.author_name, message.author_id, message.author_username)
                    ) or role_tags.get(message.author_name)
                processed.append(f"{self._format_speaker(fake_name, role_tag, reply_fake)}: {clean_body.strip()}")
            except Exception as exc:
                logger.error("Error processing structured message in anonymizer: %s", exc, exc_info=True)
                continue

        return "\n".join(processed)
    
    def clean_text_for_llm(self, session_items: List[Dict[str, Any]]) -> str:
        """
        Обрабатывает список сообщений, заменяя PII на анонимные данные.
        
        Args:
            session_items: Список словарей с ключами "text" и "ts"
            
        Returns:
            Анонимизированный текст для отправки в LLM
        """
        processed: List[str] = []
        
        for item in session_items:
            try:
                raw_text: str = item["text"]
                
                # Извлекаем информацию из заголовка
                name, username, reply_to = self._extract_user_info(raw_text)
                if not name:
                    # Если не удалось распарсить, пропускаем сообщение
                    logger.debug(f"Failed to parse header: {raw_text[:100]}")
                    continue
                
                # Пропускаем сообщения бота
                if self._should_skip_name(name):
                    continue
                
                # Получаем псевдоним для пользователя
                fake_name = self._get_fake_name(name)
                
                # Если есть reply_to, также заменяем его на псевдоним
                if reply_to:
                    reply_fake = self._get_fake_name(reply_to)
                    # Заменяем в исходном тексте reply_to на псевдоним
                    # для корректного отображения в анонимизированном виде
                    raw_text = raw_text.replace(f"(в ответ {reply_to})", f"(в ответ {reply_fake})")
                
                # Извлекаем тело сообщения (после двоеточия)
                match = self.HEADER_PATTERN.match(raw_text)
                if not match:
                    # Fallback: берем всё после первого двоеточия
                    colon_pos = raw_text.find(':')
                    if colon_pos == -1:
                        continue
                    message_body = raw_text[colon_pos + 1:].strip()
                else:
                    message_body = match.group('message')
                
                # Маскируем PII в теле сообщения
                clean_body = self._mask_pii_in_text(message_body)
                processed.append(f"{fake_name}: {clean_body.strip()}")
                
            except Exception as e:
                logger.error(f"Error processing message in anonymizer: {e}", exc_info=True)
                continue
        
        return "\n".join(processed)
    
    def decode(self, text: str) -> str:
        """
        Выполняет обратную замену псевдонимов на реальные имена.
        
        Args:
            text: Текст с псевдонимами (например, ответ от LLM)
            
        Returns:
            Текст с восстановленными реальными именами
        """
        # Сортируем по длине псевдонима в убывающем порядке,
        # чтобы избежать частичных замен (например, User_1 и User_10)
        for fake, real in sorted(self.fake_to_real.items(), 
                                key=lambda x: len(x[0]), 
                                reverse=True):
            text = text.replace(fake, real)
        
        return text
    
    def get_mapping_stats(self) -> Dict[str, Any]:
        """
        Возвращает статистику по текущим маппингам.
        
        Returns:
            Словарь со статистикой
        """
        return {
            "total_users": len(self.real_to_fake),
            "mappings": self.real_to_fake.copy(),
            "counter": self.counter
        }


# Функция для обратной совместимости с существующим кодом
def clean_text_for_llm(session_items, anonymizer: Anonymizer) -> str:
    """
    Функция-обертка для обратной совместимости.
    Использует метод clean_text_for_llm экземпляра Anonymizer.
    
    Args:
        session_items: Список сообщений
        anonymizer: Экземпляр Anonymizer
        
    Returns:
        Анонимизированный текст
    """
    return anonymizer.clean_text_for_llm(session_items)
