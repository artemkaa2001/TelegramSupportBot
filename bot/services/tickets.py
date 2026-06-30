import time
import os
import json
import secrets
from typing import Optional

from config import TICKETS_PATH, MESSAGES_PATH, TICKET_PREFIX
from bot.core.json_storage import JsonStorage

_tickets = JsonStorage(TICKETS_PATH)

def _generate_ticket_id():
    prefix = TICKET_PREFIX or "AA"
    unique = secrets.token_hex(3).upper()
    return f"{prefix}-{unique}"


def _ticket_messages_path(ticket_id: str) -> str:
    return os.path.join(MESSAGES_PATH, f"ticket_{ticket_id}.json")


async def create_ticket(user_id: str) -> str:

    data = await _tickets.load()

    new_id = _generate_ticket_id()
    while new_id in data:
        new_id = _generate_ticket_id()

    data[new_id] = {
        "user_id": str(user_id),
        "status": "open",
        "created_at": int(time.time()),
        "updated_at": int(time.time())
    }


    os.makedirs(MESSAGES_PATH, exist_ok=True)
    messages_path = _ticket_messages_path(new_id)

    with open(messages_path, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

    await _tickets.save(data)

    return new_id


async def add_message(ticket_id: str, sender: str, text: str, **extra):
    path = _ticket_messages_path(ticket_id)

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = []

    message_data = {
        "from": sender,
        "text": text,
        "time": int(time.time())
    }
    message_data.update({key: value for key, value in extra.items() if value is not None})

    history.append(message_data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


    def _update(data):
        if ticket_id in data:
            data[ticket_id]["updated_at"] = int(time.time())

    await _tickets.update(_update)


async def get_ticket(ticket_id: str) -> Optional[dict]:
    data = await _tickets.load()
    return data.get(ticket_id)


def get_ticket_messages(ticket_id: str) -> list:
    path = _ticket_messages_path(ticket_id)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def update_ticket_status(ticket_id: str, status: str):
    def _update(data):
        if ticket_id in data:
            data[ticket_id]["status"] = status
            data[ticket_id]["updated_at"] = int(time.time())

    await _tickets.update(_update)


async def get_open_tickets() -> dict:
    data = await _tickets.load()
    return {tid: t for tid, t in data.items() if t.get("status") == "open"}
