import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    DeleteMessage,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, MessageId, Update, User

# Tests use a synthetic bot and never contact Telegram or use a real token.
with patch.dict(os.environ, {"BOT_TOKEN": "123456:TEST_ONLY_NO_NETWORK", "ADMIN_ID": "100"}):
    from bot.core import utils
    from bot.core.json_storage import JsonStorage
    from bot.handlers import admin
    from bot.services import tickets
    from config import ADMIN_ID


TICKET_ID = "AA-123ABC"
USER_ID = 200
QUESTION = "Оплатил подписку, но бот снова просит оплату."
REPLY = "Здравствуйте! Оплата зачислена, всё в порядке."
NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


class RecordingSession(BaseSession):
    """Exercise real aiogram methods with an in-memory Telegram transport."""

    def __init__(self):
        super().__init__()
        self.requests = []
        self.fail_send = False
        self.fail_copy = False
        self.next_message_id = 1000

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Tests must not download content")
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            if method.reply_markup and any(
                button.url and button.url.startswith("tg://user?id=")
                for row in method.reply_markup.inline_keyboard
                for button in row
            ):
                raise TelegramBadRequest(method=method, message="BUTTON_USER_PRIVACY_RESTRICTED")
            if self.fail_send:
                raise TelegramBadRequest(method=method, message="test delivery failure")
            self.next_message_id += 1
            return Message(
                message_id=self.next_message_id,
                date=NOW,
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Support"),
                text=method.text,
            )
        if isinstance(method, CopyMessage):
            if self.fail_copy:
                raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
            return MessageId(message_id=2000)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage)):
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")


class AdminTicketTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatcher = Dispatcher()
        cls.dispatcher.include_router(admin.router)

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.history_path = root / f"ticket_{TICKET_ID}.json"
        self.history_path.write_text(
            json.dumps([{"from": "user", "text": QUESTION, "time": 1}]),
            encoding="utf-8",
        )
        ticket_path = root / "tickets.json"
        ticket_path.write_text(
            json.dumps({TICKET_ID: {"user_id": str(USER_ID), "status": "open"}}),
            encoding="utf-8",
        )
        self.enterContext(patch.object(tickets, "_tickets", JsonStorage(str(ticket_path))))
        self.enterContext(patch.object(tickets, "MESSAGES_PATH", str(root)))
        self.session = RecordingSession()
        self.bot = Bot(token="123456:TEST_ONLY_NO_NETWORK", session=self.session)
        self.enterContext(patch.object(utils, "bot", self.bot))
        self.addAsyncCleanup(self.bot.session.close)
        for state in (
            admin._admin_panels,
            admin._admin_open_msgs,
            admin._admin_notifications,
            admin._admin_reply_state,
        ):
            state.clear()
        self.update_id = 1

    async def dispatch_callback(self, data):
        update = Update(
            update_id=self.update_id,
            callback_query=CallbackQuery(
                id=f"callback-{self.update_id}",
                from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
                chat_instance="test-chat",
                data=data,
                message=Message(
                    message_id=700,
                    date=NOW,
                    chat=Chat(id=ADMIN_ID, type="private"),
                    text="Панель",
                ),
            ),
        )
        self.update_id += 1
        await self.dispatcher.feed_update(self.bot, update)

    async def dispatch_reply(self, message_id=303):
        update = Update(
            update_id=self.update_id,
            message=Message(
                message_id=message_id,
                date=NOW,
                from_user=User(id=ADMIN_ID, is_bot=False, first_name="Admin"),
                chat=Chat(id=ADMIN_ID, type="private"),
                text=REPLY,
            ),
        )
        self.update_id += 1
        await self.dispatcher.feed_update(self.bot, update)

    def requests_of_type(self, method_type):
        return [request for request in self.session.requests if isinstance(request, method_type)]

    def history(self):
        return json.loads(self.history_path.read_text(encoding="utf-8"))

    async def test_open_and_reply_deliver_via_bot_and_record_history(self):
        admin._admin_panels[ADMIN_ID] = 700
        await self.dispatch_callback(f"open:{TICKET_ID}")

        card = self.requests_of_type(SendMessage)[0]
        self.assertIn(QUESTION, card.text)
        self.assertIn(str(USER_ID), card.text)
        buttons = [button for row in card.reply_markup.inline_keyboard for button in row]
        self.assertEqual(
            {button.callback_data for button in buttons},
            {f"reply:{TICKET_ID}", f"close:{TICKET_ID}"},
        )
        self.assertTrue(all(button.url is None for button in buttons))
        self.assertIn(ADMIN_ID, admin._admin_open_msgs)
        deleted_panel = self.requests_of_type(DeleteMessage)[0]
        self.assertEqual(deleted_panel.message_id, 700)
        self.assertLess(self.session.requests.index(card), self.session.requests.index(deleted_panel))

        await self.dispatch_callback(f"reply:{TICKET_ID}")
        await self.dispatch_reply()

        delivery = self.requests_of_type(CopyMessage)
        self.assertEqual(len(delivery), 1)
        self.assertEqual(str(delivery[0].chat_id), str(USER_ID))
        self.assertEqual(delivery[0].from_chat_id, ADMIN_ID)
        self.assertEqual(delivery[0].message_id, 303)
        history = self.history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["from"], "admin")
        self.assertEqual(history[-1]["text"], REPLY)
        self.assertNotIn(ADMIN_ID, admin._admin_reply_state)
        self.assertIn("Отправлено", self.requests_of_type(SendMessage)[-1].text)

    async def test_failed_open_alerts_admin_and_preserves_panel(self):
        admin._admin_panels[ADMIN_ID] = 700
        self.session.fail_send = True
        with self.assertLogs(utils.__name__, level="WARNING"):
            await self.dispatch_callback(f"open:{TICKET_ID}")

        self.assertEqual(admin._admin_panels[ADMIN_ID], 700)
        self.assertNotIn(ADMIN_ID, admin._admin_open_msgs)
        self.assertEqual(self.requests_of_type(DeleteMessage), [])
        answers = self.requests_of_type(AnswerCallbackQuery)
        self.assertTrue(any(answer.show_alert and answer.text for answer in answers))
        self.assertEqual(len(self.history()), 1)

    async def test_failed_reply_can_be_retried_without_false_history_entry(self):
        await self.dispatch_callback(f"reply:{TICKET_ID}")
        self.session.fail_copy = True
        await self.dispatch_reply()

        self.assertEqual(len(self.history()), 1)
        self.assertEqual(admin._admin_reply_state[ADMIN_ID], TICKET_ID)
        self.assertIn("Не удалось доставить", self.requests_of_type(SendMessage)[-1].text)

        self.session.fail_copy = False
        await self.dispatch_reply(message_id=304)

        self.assertEqual(len(self.requests_of_type(CopyMessage)), 2)
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.history()[-1]["text"], REPLY)
        self.assertNotIn(ADMIN_ID, admin._admin_reply_state)


if __name__ == "__main__":
    unittest.main()
