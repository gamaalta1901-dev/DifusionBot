"""Flujo de inicio de sesión: API ID / API Hash / teléfono / OTP / 2FA.

Todo el flujo ocurre en UN SOLO mensaje ("panel") que se va actualizando, en
vez de mandar un mensaje nuevo en cada paso. Los mensajes que escribe el
usuario (credenciales, teléfono, contraseña) se borran enseguida.

El OTP NUNCA se escribe como mensaje de texto: se ingresa con un teclado
numérico inline, porque si Telegram detecta el código de login dentro de un
mensaje lo invalida automáticamente.
"""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

import storage
from keyboards import login_button, main_menu, numpad
from runtime import pending_clients
from states import Auth

router = Router()
log = logging.getLogger(__name__)

MAX_CODE_LEN = 6

WELCOME = (
    "👋 <b>Bienvenido</b>\n\n"
    "Este bot lista <b>todos los súper grupos</b> de tu cuenta de Telegram.\n\n"
    "Necesitas tu <b>API ID</b> y <b>API Hash</b>, que obtienes en\n"
    "https://my.telegram.org/auth?to=apps\n\n"
    "Pulsa el botón para iniciar sesión."
)
STEP_API_ID = "1️⃣ Envía tu <b>API ID</b> (solo números):"
STEP_API_HASH = "2️⃣ Envía tu <b>API Hash</b>:"
STEP_PHONE = (
    "3️⃣ Envía tu <b>número de teléfono</b> con código de país\n"
    "(ejemplo: <code>+5215512345678</code>):"
)


def _fmt_code(entered: str) -> str:
    shown = " ".join(entered) if entered else "—"
    return (
        "🔢 <b>Ingresa el código de Telegram</b>\n\n"
        "Usa el teclado de abajo. <b>No escribas el código como mensaje</b>: "
        "Telegram lo invalidaría.\n\n"
        f"Código: <code>{shown}</code>"
    )


# --------------------------------------------------------------------------- #
#  Utilidades del panel (mensaje único actualizable)                          #
# --------------------------------------------------------------------------- #
async def _panel(bot, state: FSMContext, text: str, reply_markup=None) -> None:
    """Edita el mensaje-panel guardado en el FSM; si no se puede, crea uno nuevo."""
    data = await state.get_data()
    chat_id = data.get("panel_chat")
    msg_id = data.get("panel_msg")

    if chat_id and msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=msg_id, reply_markup=reply_markup
            )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            # El mensaje ya no existe o no se puede editar: creamos uno nuevo.

    if chat_id:
        sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
        await state.update_data(panel_chat=sent.chat.id, panel_msg=sent.message_id)


async def _remember_panel(state: FSMContext, message: Message) -> None:
    await state.update_data(panel_chat=message.chat.id, panel_msg=message.message_id)


async def _del(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Inicio / menú                                                              #
# --------------------------------------------------------------------------- #
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _del(message)
    row = await storage.get_session(message.from_user.id)
    if row:
        await message.answer(
            "✅ Ya tienes una sesión activa.\n\n¿Qué quieres hacer?",
            reply_markup=main_menu(),
        )
        return
    sent = await message.answer(WELCOME, reply_markup=login_button())
    await _remember_panel(state, sent)


@router.callback_query(F.data == "menu:login")
async def start_login_cb(cq: CallbackQuery, state: FSMContext) -> None:
    await _remember_panel(state, cq.message)
    await state.set_state(Auth.api_id)
    await _panel(cq.bot, state, STEP_API_ID)
    await cq.answer()


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _del(message)
    sent = await message.answer(STEP_API_ID)
    await _remember_panel(state, sent)
    await state.set_state(Auth.api_id)


# --------------------------------------------------------------------------- #
#  Captura de credenciales                                                    #
# --------------------------------------------------------------------------- #
@router.message(Auth.api_id)
async def got_api_id(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await _del(message)
    if not text.isdigit():
        await _panel(message.bot, state, "⚠️ El API ID debe ser un número.\n\n" + STEP_API_ID)
        return
    await state.update_data(api_id=int(text))
    await state.set_state(Auth.api_hash)
    await _panel(message.bot, state, STEP_API_HASH)


@router.message(Auth.api_hash)
async def got_api_hash(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await _del(message)
    if len(text) < 30:
        await _panel(message.bot, state, "⚠️ Ese API Hash no parece válido.\n\n" + STEP_API_HASH)
        return
    await state.update_data(api_hash=text)
    await state.set_state(Auth.phone)
    await _panel(message.bot, state, STEP_PHONE)


@router.message(Auth.phone)
async def got_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip().replace(" ", "")
    await _del(message)
    data = await state.get_data()
    await _panel(message.bot, state, "⏳ Enviando código…")

    client = TelegramClient(StringSession(), data["api_id"], data["api_hash"])
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except (ApiIdInvalidError, PhoneNumberInvalidError) as e:
        await client.disconnect()
        await _panel(
            message.bot,
            state,
            f"❌ Datos inválidos ({type(e).__name__}). Inténtalo de nuevo:",
            reply_markup=login_button(),
        )
        await state.clear()
        return
    except Exception as e:  # FloodWait, red, etc.
        await client.disconnect()
        log.exception("send_code_request falló")
        await _panel(
            message.bot,
            state,
            f"❌ No se pudo enviar el código: {e}",
            reply_markup=login_button(),
        )
        await state.clear()
        return

    pending_clients[message.from_user.id] = client
    await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash, code="")
    await state.set_state(Auth.code)
    await _panel(message.bot, state, _fmt_code(""), reply_markup=numpad())


# --------------------------------------------------------------------------- #
#  Teclado numérico para el OTP                                               #
# --------------------------------------------------------------------------- #
@router.callback_query(Auth.code, F.data.startswith("pad:"))
async def on_pad(cq: CallbackQuery, state: FSMContext) -> None:
    action = cq.data.split(":", 1)[1]
    data = await state.get_data()
    entered = data.get("code", "")
    user_id = cq.from_user.id

    if action == "cancel":
        client = pending_clients.pop(user_id, None)
        if client:
            await client.disconnect()
        await _panel(cq.bot, state, "❌ Inicio de sesión cancelado.", reply_markup=login_button())
        await state.clear()
        await cq.answer()
        return

    if action == "ok":
        await _try_sign_in(cq, state, entered)
        return

    if action == "back":
        entered = entered[:-1]
    elif len(entered) < MAX_CODE_LEN:  # dígito
        entered += action

    await state.update_data(code=entered)
    await _panel(cq.bot, state, _fmt_code(entered), reply_markup=numpad())
    await cq.answer()


async def _try_sign_in(cq: CallbackQuery, state: FSMContext, code: str) -> None:
    user_id = cq.from_user.id
    client = pending_clients.get(user_id)
    data = await state.get_data()

    if not client:
        await _panel(cq.bot, state, "⚠️ La sesión de login expiró.", reply_markup=login_button())
        await state.clear()
        await cq.answer()
        return

    if len(code) < 4:
        await cq.answer("El código es demasiado corto.", show_alert=True)
        return

    try:
        await client.sign_in(
            phone=data["phone"],
            code=code,
            phone_code_hash=data["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        await state.set_state(Auth.password)
        await _panel(
            cq.bot,
            state,
            "🔒 Tu cuenta tiene <b>verificación en dos pasos</b>.\n\n"
            "Envía tu contraseña (borraré el mensaje enseguida):",
        )
        await cq.answer()
        return
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
        await state.update_data(code="")
        motivo = "Código inválido" if isinstance(e, PhoneCodeInvalidError) else "Código expirado"
        await _panel(cq.bot, state, f"❌ {motivo}.\n\n" + _fmt_code(""), reply_markup=numpad())
        await cq.answer()
        return
    except Exception as e:
        log.exception("sign_in falló")
        await cq.answer(f"Error: {e}", show_alert=True)
        return

    await _finish_login(cq.bot, state, user_id, client, data)
    await cq.answer("✅ Sesión iniciada")


# --------------------------------------------------------------------------- #
#  Verificación en dos pasos (2FA)                                            #
# --------------------------------------------------------------------------- #
@router.message(Auth.password)
async def got_password(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    password = message.text or ""
    await _del(message)  # borrar de inmediato la contraseña

    client = pending_clients.get(user_id)
    if not client:
        await _panel(message.bot, state, "⚠️ La sesión de login expiró.", reply_markup=login_button())
        await state.clear()
        return

    data = await state.get_data()
    try:
        await client.sign_in(password=password)
    except PasswordHashInvalidError:
        await _panel(
            message.bot,
            state,
            "❌ Contraseña incorrecta.\n\nEnvía tu contraseña de nuevo:",
        )
        return
    except Exception as e:
        log.exception("sign_in con contraseña falló")
        await _panel(message.bot, state, f"❌ Error: {e}", reply_markup=login_button())
        await state.clear()
        return

    await _finish_login(message.bot, state, user_id, client, data)


# --------------------------------------------------------------------------- #
#  Cierre del login                                                           #
# --------------------------------------------------------------------------- #
async def _finish_login(bot, state: FSMContext, user_id: int, client: TelegramClient, data: dict) -> None:
    session_string = client.session.save()
    await storage.save_session(
        user_id=user_id,
        api_id=data["api_id"],
        api_hash=data["api_hash"],
        phone=data.get("phone"),
        session_string=session_string,
    )
    await client.disconnect()
    pending_clients.pop(user_id, None)
    await _panel(
        bot,
        state,
        "✅ <b>Sesión iniciada correctamente.</b>\n\n¿Qué quieres hacer?",
        reply_markup=main_menu(),
    )
    await state.clear()


# --------------------------------------------------------------------------- #
#  Fallback                                                                   #
# --------------------------------------------------------------------------- #
@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    cur = await state.get_state()
    if cur == Auth.code.state:
        # No queremos el código como texto: lo borramos sin tocar el panel.
        await _del(message)
    elif cur is None:
        await cmd_start(message, state)
