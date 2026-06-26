import asyncio
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from bot.core.utils import safe_send, is_admin, message_to_ticket_text
from config import ADMIN_ID
from bot.services.users import (
    get_user, set_last_message, set_current_ticket,
    is_ack_planned, set_ack_planned, update_user)
from bot.services.tickets import (
    create_ticket, add_message)
from bot.services.users import get_current_ticket
from bot.services.tickets import get_ticket

router = Router()

router.message.filter(F.from_user.id != ADMIN_ID)


@router.message(Command("start"))
async def start_cmd(message: Message):
    user_id = message.from_user.id

    try:
        await message.delete()
    except:
        pass

    current_ticket = await get_current_ticket(user_id)

    if current_ticket:
        ticket = await get_ticket(current_ticket)

        if ticket and ticket.get("status") != "closed":
            return

    text = (
        "Привет! Это поддержка {example}.\n"
        "Опишите проблему (можно несколькими сообщениями), после чего ожидайте."
    )

    await safe_send(message.chat.id, text)


@router.message()
async def user_message(message: Message):
    user_id = message.from_user.id
    text = message_to_ticket_text(message)

    try:
        user = await get_user(user_id)
        ticket_id = user.get("current_ticket_id")
        if ticket_id is None:
            ticket_id = await create_ticket(user_id)
            await set_current_ticket(user_id, ticket_id)
            await update_user(user_id, ticket_fresh=True)
            await asyncio.sleep(0.1)


        await add_message(ticket_id, sender="user", text=text)
        await set_last_message(user_id)
        if not await is_ack_planned(user_id):
            await set_ack_planned(user_id, True)

            from bot.services.scheduler import schedule_ack
            await schedule_ack(user_id, ticket_id)



    except Exception as e:
        print(f"ОШИБКА В USER MESSAGE: {e}")
        import traceback
        traceback.print_exc()


