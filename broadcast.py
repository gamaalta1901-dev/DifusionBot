"""Listado paginado de súper grupos, selección múltiple y envío de difusión.

El envío se hace con la sesión Telethon del usuario (su cuenta es la que está
en los grupos). Equivalente a copy_message:
  - 'compose': se re-sube el contenido guardado (con los botones como enlaces
    al pie, porque una cuenta de usuario no puede poner botones nativos).
  - 'link': forward_messages(drop_author=True) -> copia sin cabecera de
    reenviado, conservando media y botones URL del mensaje original.
"""

import asyncio
import html
import json
import logging
import os
import re
import time
from collections import Counter
from math import ceil

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import PeerChannel

import runtime
import storage
import tele
from keyboards import login_button, main_menu

router = Router()
log = logging.getLogger(__name__)

PAGE_SIZE = 8
SEND_DELAY = 1.0          # segundos entre envíos para evitar FloodWait
MAX_FLOOD_WAIT = 60       # si Telegram pide esperar más, se omite el grupo
LINK_RE = re.compile(r"t\.me/(?:c/(\d+)|([A-Za-z0-9_]+))/(\d+)")

# Intervalos disponibles para programar (horas)
INTERVALS = (1, 3, 6, 12, 24)


class SessionInvalid(Exception):
    """La sesión Telethon del usuario ya no es válida."""


# --------------------------------------------------------------------------- #
#  Render de la lista paginada                                                 #
# --------------------------------------------------------------------------- #
def _render(user_id: int, page: int):
    grupos = runtime.group_cache.get(user_id, [])
    sel = runtime.selected.setdefault(user_id, set())
    total = len(grupos)
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    inicio = page * PAGE_SIZE
    bloque = grupos[inicio : inicio + PAGE_SIZE]

    b = InlineKeyboardBuilder()
    for nombre, username, gid in bloque:
        mark = "✅" if gid in sel else "⬜"
        disp = username or (nombre[:28] if nombre else str(gid))
        b.button(text=f"{mark} {disp}", callback_data=f"bc:tg:{page}:{gid}")
    b.adjust(1)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Atrás", callback_data=f"bc:pg:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="bc:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Siguiente ➡️", callback_data=f"bc:pg:{page+1}"))
    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="✅ Seleccionar todos", callback_data=f"bc:all:{page}"),
        InlineKeyboardButton(text="⬜ Deseleccionar", callback_data=f"bc:none:{page}"),
    )

    sid = runtime.editing_schedule.get(user_id)
    if sid:  # modo edición de grupos de una difusión programada
        b.row(InlineKeyboardButton(text=f"💾 Guardar grupos ({len(sel)})", callback_data="bc:sgsave"))
        b.row(InlineKeyboardButton(text="❌ Cancelar", callback_data=f"bc:sed:{sid}"))
        cabecera = "👥 <b>Editando grupos</b> de la difusión programada"
    else:
        b.row(InlineKeyboardButton(text=f"📤 Enviar Difusión ({len(sel)})", callback_data="bc:send"))
        b.row(InlineKeyboardButton(text="❌ Cerrar", callback_data="bc:close"))
        cabecera = "📋 <b>Súper grupos</b>"

    texto = (
        f"{cabecera} — {total} en total\n"
        f"Seleccionados: <b>{len(sel)}</b>\n"
        f"Página {page+1}/{pages}\n\n"
        "Toca para seleccionar/deseleccionar:"
    )
    return texto, b.as_markup(), page


async def _edit(cq: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await cq.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            await cq.message.answer(text, reply_markup=reply_markup)


async def _show_list(cq: CallbackQuery, page: int) -> None:
    texto, kb, _ = _render(cq.from_user.id, page)
    await _edit(cq, texto, reply_markup=kb)


# --------------------------------------------------------------------------- #
#  Abrir lista                                                                 #
# --------------------------------------------------------------------------- #
async def _load_groups(row) -> list:
    """Obtiene los súper grupos del usuario y refresca la caché. Lanza SessionInvalid."""
    client = await tele.open_client(row)
    try:
        if not await client.is_user_authorized():
            raise SessionInvalid()
        grupos, _ = await tele.fetch_supergroups(client)
    finally:
        await client.disconnect()
    runtime.group_cache[row["user_id"]] = grupos
    return grupos


@router.callback_query(F.data == "menu:list")
async def open_list(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    row = await storage.get_session(user_id)
    if not row:
        await _edit(cq, "No tienes sesión activa. Usa /start.", reply_markup=login_button())
        await cq.answer()
        return

    await cq.answer("Cargando grupos…")
    await _edit(cq, "⏳ Obteniendo tus súper grupos…")

    try:
        grupos = await _load_groups(row)
    except SessionInvalid:
        await storage.delete_session(user_id)
        await _edit(cq, "⚠️ La sesión ya no es válida. Inicia sesión de nuevo con /start.",
                    reply_markup=login_button())
        return
    except Exception as e:
        log.warning("carga de grupos falló: %s", e)
        await _edit(cq, f"❌ Error al obtener grupos: {e}", reply_markup=main_menu())
        return

    runtime.editing_schedule.pop(user_id, None)  # modo normal (no edición)
    ids = {g[2] for g in grupos}
    runtime.selected.setdefault(user_id, set()).intersection_update(ids)

    if not grupos:
        await _edit(cq, "No perteneces a ningún súper grupo.", reply_markup=main_menu())
        return
    await _show_list(cq, 0)


# --------------------------------------------------------------------------- #
#  Navegación y selección                                                      #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "bc:noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data.startswith("bc:pg:"))
async def page_nav(cq: CallbackQuery) -> None:
    page = int(cq.data.rsplit(":", 1)[1])
    await _show_list(cq, page)
    await cq.answer()


@router.callback_query(F.data.startswith("bc:tg:"))
async def toggle(cq: CallbackQuery) -> None:
    _, _, page, gid = cq.data.split(":")
    sel = runtime.selected.setdefault(cq.from_user.id, set())
    gid = int(gid)
    sel.discard(gid) if gid in sel else sel.add(gid)
    await _show_list(cq, int(page))
    await cq.answer()


@router.callback_query(F.data.startswith("bc:all:"))
async def select_all(cq: CallbackQuery) -> None:
    page = int(cq.data.rsplit(":", 1)[1])
    grupos = runtime.group_cache.get(cq.from_user.id, [])
    runtime.selected.setdefault(cq.from_user.id, set()).update(g[2] for g in grupos)
    await _show_list(cq, page)
    await cq.answer("Todos seleccionados")


@router.callback_query(F.data.startswith("bc:none:"))
async def select_none(cq: CallbackQuery) -> None:
    page = int(cq.data.rsplit(":", 1)[1])
    runtime.selected.get(cq.from_user.id, set()).clear()
    await _show_list(cq, page)
    await cq.answer("Selección vaciada")


@router.callback_query(F.data == "bc:close")
async def close_list(cq: CallbackQuery) -> None:
    runtime.editing_schedule.pop(cq.from_user.id, None)
    await _edit(cq, "Menú principal. ¿Qué quieres hacer?", reply_markup=main_menu())
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Enviar difusión: elegir mensaje guardado                                    #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "bc:send")
async def choose_message(cq: CallbackQuery) -> None:
    sel = runtime.selected.get(cq.from_user.id, set())
    if not sel:
        await cq.answer("Selecciona al menos un grupo.", show_alert=True)
        return

    mensajes = await storage.list_broadcasts(cq.from_user.id)
    if not mensajes:
        await cq.answer("No tienes mensajes guardados. Usa /mensajes.", show_alert=True)
        return

    b = InlineKeyboardBuilder()
    for m in mensajes:
        etiqueta = "🔗 " if m["kind"] == "link" else "📝 "
        b.button(text=etiqueta + m["title"], callback_data=f"bc:msg:{m['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="⬅️ Volver", callback_data="bc:pg:0"))
    await _edit(
        cq,
        f"📨 Elige el mensaje a difundir a <b>{len(sel)}</b> grupo(s):",
        reply_markup=b.as_markup(),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("bc:msg:"))
async def confirm_message(cq: CallbackQuery) -> None:
    bid = int(cq.data.rsplit(":", 1)[1])
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        return
    sel = runtime.selected.get(cq.from_user.id, set())
    b = InlineKeyboardBuilder()
    b.button(text="📤 Enviar ahora", callback_data=f"bc:go:{bid}")
    for h in INTERVALS:
        b.button(text=f"⏱ Cada {h}h", callback_data=f"bc:sch:{bid}:{h}")
    b.button(text="❌ Cancelar", callback_data="bc:pg:0")
    b.adjust(1, 2, 2, 1, 1)  # ahora / 1h-3h / 6h-12h / 24h / cancelar
    await _edit(
        cq,
        f"¿Enviar «<b>{html.escape(bc['title'])}</b>» a <b>{len(sel)}</b> grupo(s)?\n\n"
        "Elige <b>enviar ahora</b> o programar el reenvío automático "
        "(el primer envío es inmediato y luego se repite):",
        reply_markup=b.as_markup(),
    )
    await cq.answer()


# --------------------------------------------------------------------------- #
#  Enviar difusión: ejecución                                                  #
# --------------------------------------------------------------------------- #
def _buttons_footer(buttons_json: str | None) -> str:
    if not buttons_json:
        return ""
    filas = json.loads(buttons_json)
    lineas = []
    for fila in filas:
        lineas.append("   ".join(f'<a href="{btn["url"]}">🔗 {html.escape(btn["text"])}</a>' for btn in fila))
    return "\n\n" + "\n".join(lineas) if lineas else ""


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _is_media_forbidden(e: Exception) -> bool:
    """True si el grupo rechaza el tipo de media (pero no el texto plano)."""
    s = f"{type(e).__name__} {e}".upper()
    if "PLAIN" in s or "FORBIDDEN" not in s:
        return False
    return any(
        k in s
        for k in ("MEDIA", "PHOTO", "VIDEO", "DOC", "AUDIO", "VOICE", "GIF",
                  "STICKER", "ROUNDVIDEO")
    )


def _err_summary(e: Exception) -> str:
    """Traduce un error de Telethon a un motivo corto y legible."""
    name = type(e).__name__
    msg = str(e)
    nombres = {
        "ChatWriteForbiddenError": "sin permiso para escribir",
        "ChatRestrictedError": "chat restringido",
        "UserBannedInChannelError": "tu cuenta está restringida/baneada",
        "ChannelPrivateError": "sin acceso (privado o expulsado)",
        "SlowModeWaitError": "modo lento activo",
        "ChatAdminRequiredError": "requiere ser administrador",
    }
    if name in nombres:
        return nombres[name]
    if "CHAT_SEND_PLAIN_FORBIDDEN" in msg:
        return "no permite mensajes de texto (solo media)"
    if "ALLOW_PAYMENT_REQUIRED" in msg or "PAYMENT_REQUIRED" in msg:
        return "requiere pago/estrellas para escribir"
    if _is_media_forbidden(e):
        return "no permite ese tipo de media"
    return name


def _reasons_text(reasons: Counter) -> str:
    if not reasons:
        return ""
    partes = ", ".join(f"{motivo} ×{n}" for motivo, n in reasons.most_common())
    return f"\n\n<b>Motivos de fallo:</b> {html.escape(partes)}"


def _media_kwargs(media_type: str | None) -> dict:
    if media_type == "voice":
        return {"voice_note": True}
    if media_type == "video_note":
        return {"video_note": True}
    if media_type == "document":
        return {"force_document": True}
    return {}


def _parse_link(link: str):
    m = LINK_RE.search(link)
    if not m:
        raise ValueError(f"enlace inválido: {link}")
    internal, username, msg_id = m.group(1), m.group(2), int(m.group(3))
    peer = PeerChannel(int(internal)) if internal else username
    return peer, msg_id


async def _send_text(client, entity, body: str) -> None:
    """Envía texto; si el HTML está mal formado reintenta sin formato.
    Re-lanza RPCError (errores del servidor) para que el llamador decida."""
    try:
        await client.send_message(entity, body, parse_mode="html", link_preview=False)
    except RPCError:
        raise
    except Exception:
        await client.send_message(entity, _strip_html(body), link_preview=False)


async def _send_media(client, entity, media, body: str, media_type) -> None:
    try:
        await client.send_file(
            entity, media, caption=body or None, parse_mode="html",
            **_media_kwargs(media_type),
        )
    except RPCError:
        raise
    except Exception:
        await client.send_file(
            entity, media, caption=_strip_html(body) or None,
            **_media_kwargs(media_type),
        )


async def _send_one(client, entity, bc) -> None:
    if bc["kind"] == "link":
        for link in json.loads(bc["links_json"]):
            peer, msg_id = _parse_link(link)
            msg = await client.get_messages(peer, ids=msg_id)
            if msg:
                # drop_author -> copia sin "Reenviado de", conservando media/botones URL
                await client.forward_messages(entity, msg, drop_author=True)
        return

    # kind == 'compose'
    body = (bc["text"] or "") + _buttons_footer(bc["buttons_json"])
    has_media = bool(bc["media_path"] and os.path.exists(bc["media_path"]))
    has_text = bool(body.strip())

    if has_media:
        try:
            await _send_media(client, entity, bc["media_path"], body, bc["media_type"])
        except RPCError as e:
            # Si el grupo no permite media pero sí texto, manda solo el texto.
            if has_text and _is_media_forbidden(e):
                await _send_text(client, entity, body)
            else:
                raise
    elif has_text:
        await _send_text(client, entity, body)


async def broadcast_message(row, bc, group_ids: list[int]) -> tuple[int, int]:
    """Envía `bc` a `group_ids` con la sesión Telethon de `row`. Devuelve (ok, fail).

    Reutilizado por el envío manual y por el planificador.
    Lanza SessionInvalid si la sesión ya no está autorizada.
    """
    client = await tele.open_client(row)
    try:
        if not await client.is_user_authorized():
            raise SessionInvalid()
        _, entidades = await tele.fetch_supergroups(client)

        ok = fail = 0
        reasons: Counter = Counter()
        for gid in group_ids:
            entity = entidades.get(gid)
            if entity is None:
                fail += 1
                reasons["no estás en el grupo"] += 1
                continue
            try:
                await _send_one(client, entity, bc)
                ok += 1
            except FloodWaitError as e:
                if e.seconds <= MAX_FLOOD_WAIT:
                    await asyncio.sleep(e.seconds + 1)
                    try:
                        await _send_one(client, entity, bc)
                        ok += 1
                    except Exception as e2:
                        motivo = _err_summary(e2)
                        log.warning("Grupo %s falló tras espera: %s", gid, motivo)
                        reasons[motivo] += 1
                        fail += 1
                else:
                    log.warning("Grupo %s omitido: espera de %ss", gid, e.seconds)
                    reasons["límite de Telegram (espera larga)"] += 1
                    fail += 1
            except Exception as e:
                motivo = _err_summary(e)
                log.warning("Grupo %s omitido: %s", gid, motivo)
                reasons[motivo] += 1
                fail += 1
            await asyncio.sleep(SEND_DELAY)
        return ok, fail, reasons
    finally:
        await client.disconnect()


@router.callback_query(F.data.startswith("bc:go:"))
async def do_broadcast(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    bid = int(cq.data.rsplit(":", 1)[1])

    row = await storage.get_session(user_id)
    if not row:
        await _edit(cq, "No tienes sesión activa. Usa /start.", reply_markup=login_button())
        await cq.answer()
        return
    bc = await storage.get_broadcast(bid, user_id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        return
    sel = list(runtime.selected.get(user_id, set()))
    if not sel:
        await cq.answer("No hay grupos seleccionados.", show_alert=True)
        return

    await cq.answer("Enviando…")
    await _edit(cq, f"📡 Enviando «{html.escape(bc['title'])}» a {len(sel)} grupo(s)…")

    try:
        ok, fail, reasons = await broadcast_message(row, bc, sel)
    except SessionInvalid:
        await storage.delete_session(user_id)
        await _edit(cq, "⚠️ La sesión ya no es válida. Inicia sesión con /start.",
                    reply_markup=login_button())
        return
    except Exception as e:
        log.warning("difusión falló: %s", e)
        await _edit(cq, f"❌ Error en la difusión: {e}", reply_markup=main_menu())
        return

    await _edit(
        cq,
        f"✅ Difusión «<b>{html.escape(bc['title'])}</b>» terminada.\n\n"
        f"Enviados: <b>{ok}</b> · Fallidos: <b>{fail}</b> · Total: {len(sel)}"
        + _reasons_text(reasons),
        reply_markup=main_menu(),
    )


# --------------------------------------------------------------------------- #
#  Programar difusión recurrente                                               #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("bc:sch:"))
async def schedule_broadcast(cq: CallbackQuery) -> None:
    _, _, bid, hours = cq.data.split(":")
    bid, hours = int(bid), int(hours)
    user_id = cq.from_user.id

    row = await storage.get_session(user_id)
    if not row:
        await _edit(cq, "No tienes sesión activa. Usa /start.", reply_markup=login_button())
        await cq.answer()
        return
    bc = await storage.get_broadcast(bid, user_id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        return
    sel = list(runtime.selected.get(user_id, set()))
    if not sel:
        await cq.answer("No hay grupos seleccionados.", show_alert=True)
        return

    interval = hours * 3600
    await cq.answer("Programando…")
    await _edit(cq, f"📡 Enviando «{html.escape(bc['title'])}» ahora y programando cada {hours}h…")

    try:
        ok, fail, reasons = await broadcast_message(row, bc, sel)
    except SessionInvalid:
        await storage.delete_session(user_id)
        await _edit(cq, "⚠️ La sesión ya no es válida. Inicia sesión con /start.",
                    reply_markup=login_button())
        return
    except Exception as e:
        log.warning("difusión inicial falló: %s", e)
        await _edit(cq, f"❌ Error en la difusión: {e}", reply_markup=main_menu())
        return

    await storage.add_schedule(
        user_id, bid, json.dumps(sel), interval, time.time() + interval
    )
    await _edit(
        cq,
        f"✅ Enviada ahora a <b>{ok}</b>/{len(sel)} grupo(s) (fallidos: {fail}).\n\n"
        f"⏱ <b>Programada cada {hours}h</b> — siguiente envío en {hours}h.\n"
        "Gestiona o cancela en «Difusiones programadas»."
        + _reasons_text(reasons),
        reply_markup=main_menu(),
    )


async def _render_schedules(cq: CallbackQuery) -> None:
    rows = await storage.list_schedules(cq.from_user.id)
    if not rows:
        await _edit(cq, "No tienes difusiones programadas.", reply_markup=main_menu())
        return

    b = InlineKeyboardBuilder()
    for s in rows:
        bc = await storage.get_broadcast(s["broadcast_id"], cq.from_user.id)
        title = bc["title"] if bc else "(eliminado)"
        hrs = s["interval_seconds"] // 3600
        ngrupos = len(json.loads(s["group_ids"]))
        b.button(
            text=f"⚙️ «{title}» · cada {hrs}h · {ngrupos}g",
            callback_data=f"bc:sed:{s['id']}",
        )
    b.adjust(1)
    b.row(InlineKeyboardButton(text="⬅️ Menú", callback_data="bc:close"))
    await _edit(
        cq,
        "⏱ <b>Difusiones programadas</b>\n\nToca una para editarla o cancelarla:",
        reply_markup=b.as_markup(),
    )


async def _render_schedule_detail(cq: CallbackQuery, sid: int) -> None:
    sch = await storage.get_schedule(sid, cq.from_user.id)
    if not sch:
        await _render_schedules(cq)
        return
    bc = await storage.get_broadcast(sch["broadcast_id"], cq.from_user.id)
    title = bc["title"] if bc else "(mensaje eliminado)"
    hrs = sch["interval_seconds"] // 3600
    ngrupos = len(json.loads(sch["group_ids"]))
    restante = max(0, int(sch["next_run"] - time.time())) // 60

    b = InlineKeyboardBuilder()
    b.button(text="👥 Editar grupos", callback_data=f"bc:sgedit:{sid}")
    b.button(text="📝 Cambiar mensaje", callback_data=f"bc:smsg:{sid}")
    b.button(text="⏱ Cambiar intervalo", callback_data=f"bc:sint:{sid}")
    b.button(text="🗑 Cancelar difusión", callback_data=f"bc:schdel:{sid}")
    b.button(text="⬅️ Volver", callback_data="menu:schedules")
    b.adjust(1)
    await _edit(
        cq,
        f"⏱ <b>Difusión programada</b>\n\n"
        f"Mensaje: «<b>{html.escape(title)}</b>»\n"
        f"Intervalo: cada {hrs}h\n"
        f"Grupos: {ngrupos}\n"
        f"Próximo envío: en {restante} min",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "menu:schedules")
async def menu_schedules(cq: CallbackQuery) -> None:
    runtime.editing_schedule.pop(cq.from_user.id, None)
    await _render_schedules(cq)
    await cq.answer()


@router.callback_query(F.data.startswith("bc:sed:"))
async def schedule_detail(cq: CallbackQuery) -> None:
    runtime.editing_schedule.pop(cq.from_user.id, None)
    sid = int(cq.data.rsplit(":", 1)[1])
    await _render_schedule_detail(cq, sid)
    await cq.answer()


@router.callback_query(F.data.startswith("bc:schdel:"))
async def cancel_schedule(cq: CallbackQuery) -> None:
    sid = int(cq.data.rsplit(":", 1)[1])
    await storage.delete_schedule(sid, cq.from_user.id)
    runtime.editing_schedule.pop(cq.from_user.id, None)
    await _render_schedules(cq)
    await cq.answer("Difusión programada cancelada")


# --------------------------------------------------------------------------- #
#  Editar una difusión programada                                              #
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("bc:sgedit:"))
async def schedule_edit_groups(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    sid = int(cq.data.rsplit(":", 1)[1])
    sch = await storage.get_schedule(sid, user_id)
    if not sch:
        await cq.answer("Esa difusión ya no existe.", show_alert=True)
        await _render_schedules(cq)
        return
    row = await storage.get_session(user_id)
    if not row:
        await _edit(cq, "No tienes sesión activa. Usa /start.", reply_markup=login_button())
        await cq.answer()
        return

    await cq.answer("Cargando grupos…")
    await _edit(cq, "⏳ Obteniendo tus súper grupos…")
    try:
        grupos = await _load_groups(row)
    except SessionInvalid:
        await storage.delete_session(user_id)
        await _edit(cq, "⚠️ La sesión ya no es válida. Inicia sesión con /start.",
                    reply_markup=login_button())
        return
    except Exception as e:
        log.warning("carga de grupos falló: %s", e)
        await _edit(cq, f"❌ Error al obtener grupos: {e}", reply_markup=main_menu())
        return

    ids = {g[2] for g in grupos}
    runtime.editing_schedule[user_id] = sid
    runtime.selected[user_id] = set(json.loads(sch["group_ids"])) & ids
    await _show_list(cq, 0)


@router.callback_query(F.data == "bc:sgsave")
async def schedule_save_groups(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    sid = runtime.editing_schedule.get(user_id)
    if not sid:
        await cq.answer("Sesión de edición expirada.", show_alert=True)
        await _render_schedules(cq)
        return
    sel = list(runtime.selected.get(user_id, set()))
    if not sel:
        await cq.answer("Selecciona al menos un grupo.", show_alert=True)
        return
    await storage.update_schedule(sid, user_id, group_ids=json.dumps(sel))
    runtime.editing_schedule.pop(user_id, None)
    await _render_schedule_detail(cq, sid)
    await cq.answer("Grupos actualizados")


@router.callback_query(F.data.startswith("bc:smsg:"))
async def schedule_change_message(cq: CallbackQuery) -> None:
    sid = int(cq.data.rsplit(":", 1)[1])
    sch = await storage.get_schedule(sid, cq.from_user.id)
    if not sch:
        await cq.answer("Esa difusión ya no existe.", show_alert=True)
        await _render_schedules(cq)
        return
    mensajes = await storage.list_broadcasts(cq.from_user.id)
    if not mensajes:
        await cq.answer("No tienes mensajes guardados. Usa /mensajes.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    for m in mensajes:
        etiqueta = "🔗 " if m["kind"] == "link" else "📝 "
        b.button(text=etiqueta + m["title"], callback_data=f"bc:smset:{sid}:{m['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="⬅️ Volver", callback_data=f"bc:sed:{sid}"))
    await _edit(cq, "📝 Elige el nuevo mensaje para esta difusión:", reply_markup=b.as_markup())
    await cq.answer()


@router.callback_query(F.data.startswith("bc:smset:"))
async def schedule_set_message(cq: CallbackQuery) -> None:
    _, _, sid, bid = cq.data.split(":")
    sid, bid = int(sid), int(bid)
    bc = await storage.get_broadcast(bid, cq.from_user.id)
    if not bc:
        await cq.answer("Ese mensaje ya no existe.", show_alert=True)
        return
    await storage.update_schedule(sid, cq.from_user.id, broadcast_id=bid)
    await _render_schedule_detail(cq, sid)
    await cq.answer("Mensaje actualizado")


@router.callback_query(F.data.startswith("bc:sint:"))
async def schedule_change_interval(cq: CallbackQuery) -> None:
    sid = int(cq.data.rsplit(":", 1)[1])
    if not await storage.get_schedule(sid, cq.from_user.id):
        await cq.answer("Esa difusión ya no existe.", show_alert=True)
        await _render_schedules(cq)
        return
    b = InlineKeyboardBuilder()
    for h in INTERVALS:
        b.button(text=f"⏱ Cada {h}h", callback_data=f"bc:sintset:{sid}:{h}")
    b.button(text="⬅️ Volver", callback_data=f"bc:sed:{sid}")
    b.adjust(2, 2, 1, 1)
    await _edit(cq, "⏱ Elige el nuevo intervalo:", reply_markup=b.as_markup())
    await cq.answer()


@router.callback_query(F.data.startswith("bc:sintset:"))
async def schedule_set_interval(cq: CallbackQuery) -> None:
    _, _, sid, hours = cq.data.split(":")
    sid, hours = int(sid), int(hours)
    interval = hours * 3600
    await storage.update_schedule(
        sid, cq.from_user.id, interval_seconds=interval, next_run=time.time() + interval
    )
    await _render_schedule_detail(cq, sid)
    await cq.answer(f"Intervalo: cada {hours}h")
