"""Comando /mensajes: crear, editar y eliminar mensajes de difusión."""

import html
import json
import logging
import os
import re
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import runtime
import storage
from keyboards import yes_no_buttons
from states import MsgSave

router = Router()
log = logging.getLogger(__name__)

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
TITLE_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")

PROMPT_NEW = (
    "📝 Envíame el mensaje que quieres guardar.\n"
    "Puede ser texto, foto, video, audio, documento, etc."
)
PROMPT_BUTTONS = (
    "🔘 Envía los botones, uno por <b>línea</b>.\n\n"
    "Formato: <code>Texto - https://url</code>\n"
    "Para varios botones en la misma fila sepáralos con <code>|</code>:\n"
    "<code>Canal - https://t.me/x | Chat - https://t.me/y</code>"
)
PROMPT_TITLE = (
    "🏷️ Dale un <b>título</b> a este mensaje.\n"
    "Solo letras y números (A-Z, 0-9), sin espacios ni símbolos."
)
PROMPT_LINKS = (
    "🔗 Envía 1 o más <b>enlaces</b> de mensajes de Telegram (uno por línea).\n"
    "Ejemplos: <code>https://t.me/canal/123</code> o <code>https://t.me/c/123456/78</code>"
)


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _extract_media(message: Message) -> tuple[str | None, str | None, str]:
    """Devuelve (file_id, media_type, extensión) del mensaje, o (None, None, '')."""
    if message.photo:
        return message.photo[-1].file_id, "photo", ".jpg"
    if message.video:
        return message.video.file_id, "video", ".mp4"
    if message.animation:
        return message.animation.file_id, "animation", ".mp4"
    if message.voice:
        return message.voice.file_id, "voice", ".ogg"
    if message.video_note:
        return message.video_note.file_id, "video_note", ".mp4"
    if message.audio:
        ext = os.path.splitext(message.audio.file_name or "")[1] or ".mp3"
        return message.audio.file_id, "audio", ext
    if message.document:
        ext = os.path.splitext(message.document.file_name or "")[1] or ".bin"
        return message.document.file_id, "document", ext
    if message.sticker:
        return message.sticker.file_id, "document", ".webp"
    return None, None, ""


def _parse_buttons(text: str):
    """Convierte el texto en filas de botones [[{text,url}], ...] o None si hay error."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fila = []
        for chunk in line.split("|"):
            chunk = chunk.strip()
            if " - " not in chunk:
                return None
            label, url = chunk.rsplit(" - ", 1)
            label, url = label.strip(), url.strip()
            if not label or not url.lower().startswith(("http://", "https://")):
                return None
            fila.append({"text": label, "url": url})
        if fila:
            rows.append(fila)
    return rows or None


def _parse_links(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if "t.me/" in ln]


def _remove_media(path: str | None) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def _persist_compose(user_id: int, data: dict, title: str) -> None:
    buttons_json = json.dumps(data["buttons"]) if data.get("buttons") else None
    edit_id = data.get("edit_id")
    if edit_id:
        old = await storage.get_broadcast(edit_id, user_id)
        if old and old["media_path"] and old["media_path"] != data.get("media_path"):
            _remove_media(old["media_path"])
        await storage.update_broadcast(
            edit_id, user_id, kind="compose",
            text=data.get("text"), media_path=data.get("media_path"),
            media_type=data.get("media_type"), buttons_json=buttons_json,
        )
    else:
        await storage.add_broadcast(
            user_id=user_id, title=title, kind="compose",
            text=data.get("text"), media_path=data.get("media_path"),
            media_type=data.get("media_type"), buttons_json=buttons_json,
        )


async def _persist_link(user_id: int, data: dict, title: str) -> None:
    links_json = json.dumps(data["links"])
    edit_id = data.get("edit_id")
    if edit_id:
        await storage.update_broadcast(edit_id, user_id, kind="link", links_json=links_json)
    else:
        await storage.add_broadcast(user_id=user_id, title=title, kind="link", links_json=links_json)


# --------------------------------------------------------------------------- #
#  Vista principal: lista de mensajes guardados                                #
# --------------------------------------------------------------------------- #
async def _home_markup(user_id: int):
    rows = await storage.list_broadcasts(user_id)
    b = InlineKeyboardBuilder()
    b.button(text="📝 Mensaje nuevo", callback_data="msg:new")
    b.button(text="🔗 Enviar Enlace", callback_data="msg:link")
    for m in rows:
        etiqueta = "🔗 " if m["kind"] == "link" else "📝 "
        b.button(text=etiqueta + m["title"], callback_data=f"msg:view:{m['id']}")
    b.adjust(2, *([1] * len(rows)))
    return b.as_markup(), len(rows)


def _home_text(n: int) -> str:
    base = "💾 <b>Mensajes de difusión</b>\n\n"
    if n:
        return base + "Toca un mensaje para <b>editar</b> o <b>eliminar</b>, o crea uno nuevo."
    return base + "Aún no tienes mensajes guardados. Crea uno nuevo."


async def _show_home(cq: CallbackQuery) -> None:
    kb, n = await _home_markup(cq.from_user.id)
    try:
        await cq.message.edit_text(_home_text(n), reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            await cq.message.answer(_home_text(n), reply_markup=kb)


@router.message(Command("mensajes"))
async def cmd_mensajes(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb, n = await _home_markup(message.from_user.id)
    await message.answer(_home_text(n), reply_markup=kb)


@router.callback_query(F.data == "menu:messages")
async def menu_messages(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_home(cq)
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Detalle de un mensaje: editar / eliminar                                    #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("msg:view:"))
async def msg_view(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    bid = int(cq.data.rsplit(":", 1)[1])
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        await _show_home(cq)
        return

    if bc["kind"] == "link":
        detalle = f"Tipo: enlace(s) ({len(json.loads(bc['links_json']))})"
    else:
        extra = []
        if bc["media_type"]:
            extra.append(f"media: {bc['media_type']}")
        if bc["buttons_json"]:
            extra.append("con botones")
        detalle = "Tipo: compuesto" + (f" ({', '.join(extra)})" if extra else "")

    b = InlineKeyboardBuilder()
    b.button(text="✏️ Editar mensaje", callback_data=f"msg:edit:{bid}")
    b.button(text="🗑 Eliminar mensaje", callback_data=f"msg:del:{bid}")
    b.button(text="⬅️ Volver", callback_data="menu:messages")
    b.adjust(1)
    await cq.message.edit_text(
        f"💾 «<b>{html.escape(bc['title'])}</b>»\n{detalle}", reply_markup=b.as_markup()
    )
    await cq.answer()


@router.callback_query(F.data.startswith("msg:edit:"))
async def msg_edit(cq: CallbackQuery, state: FSMContext) -> None:
    bid = int(cq.data.rsplit(":", 1)[1])
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        await _show_home(cq)
        return

    await state.clear()
    await state.update_data(edit_id=bid, edit_title=bc["title"])
    prefijo = f"✏️ Editando «{bc['title']}» (el título se conserva).\n\n"
    if bc["kind"] == "link":
        await state.set_state(MsgSave.link_wait_links)
        await cq.message.answer(prefijo + PROMPT_LINKS)
    else:
        await state.set_state(MsgSave.new_wait_message)
        await cq.message.answer(prefijo + PROMPT_NEW)
    await cq.answer()


@router.callback_query(F.data.startswith("msg:del:"))
async def msg_del_confirm(cq: CallbackQuery) -> None:
    bid = int(cq.data.rsplit(":", 1)[1])
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        await _show_home(cq)
        return
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Sí, eliminar", callback_data=f"msg:delok:{bid}")
    b.button(text="⬅️ No", callback_data=f"msg:view:{bid}")
    b.adjust(1)
    await cq.message.edit_text(
        f"¿Eliminar «<b>{html.escape(bc['title'])}</b>»?\n"
        "Se borrará también su media y sus difusiones programadas. No se puede deshacer.",
        reply_markup=b.as_markup(),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("msg:delok:"))
async def msg_del_ok(cq: CallbackQuery) -> None:
    bid = int(cq.data.rsplit(":", 1)[1])
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if bc and bc["media_path"]:
        _remove_media(bc["media_path"])
    await storage.delete_schedules_for_broadcast(cq.from_user.id, bid)
    await storage.delete_broadcast(bid, cq.from_user.id)
    await cq.answer("Mensaje eliminado")
    await _show_home(cq)


# --------------------------------------------------------------------------- #
#  Cancelar (global)                                                           #
# --------------------------------------------------------------------------- #
@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("No hay ninguna operación en curso.")
        return
    client = runtime.pending_clients.pop(message.from_user.id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    await state.clear()
    await message.answer("✖️ Operación cancelada. Usa /start.")


# --------------------------------------------------------------------------- #
#  Flujo "Mensaje nuevo" (también se reutiliza al editar)                       #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "msg:new")
async def msg_new(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MsgSave.new_wait_message)
    await cq.message.answer(PROMPT_NEW)
    await cq.answer()


@router.message(MsgSave.new_wait_message)
async def new_got_message(message: Message, state: FSMContext) -> None:
    file_id, media_type, ext = _extract_media(message)

    media_path = None
    if file_id:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        media_path = os.path.join(MEDIA_DIR, f"{uuid4().hex}{ext}")
        try:
            await message.bot.download(file_id, destination=media_path)
        except Exception as e:
            log.warning("descarga de media falló: %s", e)
            await message.answer(f"❌ No pude descargar el archivo: {e}\nIntenta de nuevo:")
            return

    try:
        html_text = message.html_text
    except Exception:
        html_text = None
    html_text = html_text or None

    if not media_path and not html_text:
        await message.answer("⚠️ Ese mensaje está vacío. Envía texto o un archivo:")
        return

    await state.update_data(
        kind="compose", text=html_text, media_path=media_path,
        media_type=media_type, buttons=None,
    )
    await state.set_state(MsgSave.new_ask_buttons)
    await message.answer("¿Quieres añadir <b>botones</b> (URL) al mensaje?", reply_markup=yes_no_buttons())


@router.callback_query(MsgSave.new_ask_buttons, F.data == "msg:btn_yes")
async def new_buttons_yes(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MsgSave.new_wait_buttons)
    await cq.message.answer(PROMPT_BUTTONS)
    await cq.answer()


@router.callback_query(MsgSave.new_ask_buttons, F.data == "msg:btn_no")
async def new_buttons_no(cq: CallbackQuery, state: FSMContext) -> None:
    await _compose_after_buttons(cq.message, cq.from_user.id, state)
    await cq.answer()


@router.message(MsgSave.new_wait_buttons)
async def new_got_buttons(message: Message, state: FSMContext) -> None:
    rows = _parse_buttons(message.text or "")
    if rows is None:
        await message.answer("⚠️ Formato inválido. Usa <code>Texto - https://url</code> por línea:")
        return
    await state.update_data(buttons=rows)
    await _compose_after_buttons(message, message.from_user.id, state)


async def _compose_after_buttons(target: Message, user_id: int, state: FSMContext) -> None:
    """Tras definir los botones: si es edición guarda directo; si no, pide título."""
    data = await state.get_data()
    if data.get("edit_id"):
        await _persist_compose(user_id, data, data["edit_title"])
        await state.clear()
        await target.answer(f"✅ Mensaje «<b>{html.escape(data['edit_title'])}</b>» actualizado.")
    else:
        await state.set_state(MsgSave.new_wait_title)
        await target.answer(PROMPT_TITLE)


@router.message(MsgSave.new_wait_title)
async def new_got_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not TITLE_RE.match(title):
        await message.answer("⚠️ Título inválido. Solo A-Z y 0-9, sin espacios:")
        return
    data = await state.get_data()
    await _persist_compose(message.from_user.id, data, title)
    await state.clear()
    await message.answer(f"✅ Mensaje guardado como «<b>{title}</b>».")


# --------------------------------------------------------------------------- #
#  Flujo "Enviar Enlace" (también se reutiliza al editar)                       #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "msg:link")
async def msg_link(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MsgSave.link_wait_links)
    await cq.message.answer(PROMPT_LINKS)
    await cq.answer()


@router.message(MsgSave.link_wait_links)
async def link_got_links(message: Message, state: FSMContext) -> None:
    links = _parse_links(message.text or "")
    if not links:
        await message.answer("⚠️ No encontré enlaces válidos de t.me. Inténtalo de nuevo:")
        return
    await state.update_data(links=links)
    data = await state.get_data()
    if data.get("edit_id"):
        await _persist_link(message.from_user.id, data, data["edit_title"])
        await state.clear()
        await message.answer(f"✅ Mensaje «<b>{html.escape(data['edit_title'])}</b>» actualizado.")
    else:
        await state.set_state(MsgSave.link_wait_title)
        await message.answer(PROMPT_TITLE)


@router.message(MsgSave.link_wait_title)
async def link_got_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not TITLE_RE.match(title):
        await message.answer("⚠️ Título inválido. Solo A-Z y 0-9, sin espacios:")
        return
    data = await state.get_data()
    await _persist_link(message.from_user.id, data, title)
    await state.clear()
    n = len(data["links"])
    await message.answer(f"✅ Guardado como «<b>{title}</b>» ({n} enlace(s)).")
