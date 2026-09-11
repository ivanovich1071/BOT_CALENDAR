"""Старт, профиль и справка."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import services, texts
from app.bot.db import run_db
from app.bot.deps import current_client, display_name
from app.bot.keyboards.menu import ask_phone, main_menu
from app.services import staff_notify

router = Router(name="start")


# Раньше обычного /start: ссылка привязки Telegram сотрудника из админки
@router.message(CommandStart(deep_link=True, magic=F.args.startswith(staff_notify.PAYLOAD_PREFIX)))
async def start_staff(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    token = command.args[len(staff_notify.PAYLOAD_PREFIX):]
    name = await run_db(staff_notify.consume_link_token, token, message.from_user.id)
    text = texts.STAFF_LINKED.format(name=escape(name, quote=False)) if name else texts.STAFF_LINK_EXPIRED
    await message.answer(text, reply_markup=main_menu())


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    client = await current_client(message.from_user)
    company = await run_db(services.company_profile)
    await message.answer(
        texts.greeting(company, display_name(message.from_user)),
        reply_markup=main_menu(),
    )
    if not client["phone"]:
        await message.answer(texts.ASK_PHONE, reply_markup=ask_phone())


@router.message(F.contact)
async def save_phone(message: Message) -> None:
    client = await current_client(message.from_user)
    await run_db(services.set_phone, client["id"], message.contact.phone_number)
    await message.answer(texts.PHONE_SAVED, reply_markup=main_menu())


@router.message(F.text == texts.BTN_SKIP)
async def skip_phone(message: Message) -> None:
    await message.answer(texts.PHONE_SKIPPED, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED_ACTION, reply_markup=main_menu())


@router.message(Command("help"))
@router.message(F.text == texts.BTN_INFO)
async def info(message: Message) -> None:
    company = await run_db(services.company_profile)
    items = await run_db(services.active_services)
    await message.answer(texts.info(company, items), reply_markup=main_menu())
