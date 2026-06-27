"""Cierre de sesión (logout)."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

import storage
import tele
from keyboards import login_button

router = Router()
log = logging.getLogger(__name__)


async def _edit(cq: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await cq.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            await cq.message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data == "menu:logout")
async def logout(cq: CallbackQuery) -> None:
    row = await storage.get_session(cq.from_user.id)
    if not row:
        await cq.answer("No hay sesión.")
        return

    await cq.answer("Cerrando sesión…")
    client = None
    try:
        client = await tele.open_client(row)
        await client.log_out()  # revoca la sesión del lado de Telegram
    except Exception:
        log.exception("logout falló")
    finally:
        if client:
            await client.disconnect()
        await storage.delete_session(cq.from_user.id)

    await _edit(cq, "🚪 Sesión cerrada y eliminada.", reply_markup=login_button())
