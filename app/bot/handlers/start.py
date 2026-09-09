"""Старт, профиль и справка."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import services, texts
from app.bot.db import run_db
from app.bot.deps import current_client, display_name
from app.bot.keyboards.menu import ask_phone, main_menu

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    client = await current_client(message.from_user)
    await message.answer(
        texts.GREETING.format(name=display_name(message.from_user)),
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
    await message.answer(texts.INFO, reply_markup=main_menu())
