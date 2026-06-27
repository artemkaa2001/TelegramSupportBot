from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message
import asyncio
import html
from config import ADMIN_ID
from bot.core.loader import bot

TELEGRAM_TEXT_LIMIT = 4096
SAFE_TEXT_LIMIT = 3900


async def safe_send(chat_id: int, text: str, reply_markup=None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramForbiddenError:
        return None
    except TelegramBadRequest:
        return None
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await safe_send(chat_id, text, reply_markup)
    except Exception:
        return None


async def safe_send_long(chat_id: int, text: str, reply_markup=None):
    """Send long text as several Telegram messages.

    Telegram rejects messages longer than 4096 characters. The reply markup is
    attached only to the last chunk, so inline ticket controls stay usable.
    """
    chunks = split_text(text)
    last_message = None

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        last_message = await safe_send(
            chat_id,
            chunk,
            reply_markup=reply_markup if is_last else None,
        )

    return last_message


def split_text(text: str, limit: int = SAFE_TEXT_LIMIT) -> list[str]:
    if not text:
        return [""]

    chunks = []
    remaining = text

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        split_at = _avoid_html_entity_split(remaining, split_at)

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks or [""]


def _avoid_html_entity_split(text: str, split_at: int) -> int:
    last_amp = text.rfind("&", 0, split_at)
    last_semicolon = text.rfind(";", 0, split_at)

    if last_amp > last_semicolon:
        next_semicolon = text.find(";", split_at)
        if next_semicolon != -1 and next_semicolon - split_at < 16:
            return next_semicolon + 1
        if last_amp > 0:
            return last_amp

    return split_at


def escape_text(value) -> str:
    return html.escape(str(value or ""), quote=False)


def ticket_display_text(value) -> str:
    """Normalize stored ticket text before showing it to admin."""
    text = str(value or "")
    return text.replace("\\n", "\n")


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def message_to_ticket_text(message: Message) -> str:
    """Convert any supported Telegram message into an admin-friendly summary."""
    caption = getattr(message, "caption", None)
    content_type = getattr(message, "content_type", None) or "message"

    if message.text:
        return message.text

    parts = []

    if message.voice:
        parts.append(_media_line("🎙 Голосовое сообщение", message.voice.duration))
    elif message.video_note:
        parts.append(_media_line("⭕️ Видео-кружок", message.video_note.duration))
    elif message.photo:
        parts.append("🖼 Фото — вложение прикреплено ниже")
    elif message.video:
        parts.append(_media_line("🎬 Видео", message.video.duration))
    elif message.document:
        filename = f" ({message.document.file_name})" if message.document.file_name else ""
        parts.append(f"📎 Документ{filename} — вложение прикреплено ниже")
    elif message.audio:
        title = f" ({message.audio.title})" if message.audio.title else ""
        parts.append(_media_line(f"🎵 Аудио{title}", message.audio.duration))
    elif message.animation:
        parts.append("🧩 GIF/анимация — вложение прикреплено ниже")
    elif message.sticker:
        emoji = f" {message.sticker.emoji}" if message.sticker.emoji else ""
        parts.append(f"🙂 Стикер{emoji} — вложение прикреплено ниже")
    elif message.location:
        lat = message.location.latitude
        lon = message.location.longitude
        parts.append(
            "📍 Локация\n"
            f"координаты: {lat}, {lon}\n"
            f"карта: https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        )
    elif message.venue:
        lat = message.venue.location.latitude
        lon = message.venue.location.longitude
        parts.append(
            "🏛 Место\n"
            f"название: {message.venue.title}\n"
            f"адрес: {message.venue.address}\n"
            f"координаты: {lat}, {lon}\n"
            f"карта: https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        )
    elif message.contact:
        parts.append(
            "👤 Контакт\n"
            f"имя: {message.contact.first_name or ''} {message.contact.last_name or ''}\n"
            f"телефон: {message.contact.phone_number}"
        )
    elif message.poll:
        options = "\n".join(f"- {option.text}" for option in message.poll.options)
        parts.append(f"📊 Опрос\nвопрос: {message.poll.question}\nварианты:\n{options}")
    elif message.dice:
        parts.append(f"🎲 Dice {message.dice.emoji}: {message.dice.value}")
    else:
        parts.append(f"📦 Сообщение типа: {content_type}")

    if caption:
        parts.append(f"Подпись: {caption}")

    return "\n".join(parts)


def message_storage_meta(message: Message) -> dict:
    """Save enough Telegram metadata to copy original user messages to admin."""
    return {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "content_type": getattr(message, "content_type", None) or "message",
    }


def should_copy_to_admin(message_data: dict) -> bool:
    return (
        message_data.get("from") == "user"
        and message_data.get("chat_id") is not None
        and message_data.get("message_id") is not None
        and message_data.get("content_type") != "text"
    )


def _media_line(title: str, duration: int | None = None) -> str:
    duration_line = f" ({duration} сек.)" if duration else ""
    return f"{title}{duration_line} — вложение прикреплено ниже"
