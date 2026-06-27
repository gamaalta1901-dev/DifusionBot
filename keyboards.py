from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def numpad() -> InlineKeyboardMarkup:
    """Teclado numérico inline para ingresar el OTP sin escribirlo como mensaje."""
    b = InlineKeyboardBuilder()
    for n in range(1, 10):
        b.button(text=str(n), callback_data=f"pad:{n}")
    b.button(text="🔙", callback_data="pad:back")
    b.button(text="0", callback_data="pad:0")
    b.button(text="✅", callback_data="pad:ok")
    b.adjust(3, 3, 3, 3)
    b.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="pad:cancel"))
    return b.as_markup()


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Listar súper grupos", callback_data="menu:list")
    b.button(text="💾 Mensajes guardados", callback_data="menu:messages")
    b.button(text="⏱ Difusiones programadas", callback_data="menu:schedules")
    b.button(text="🚪 Cerrar sesión", callback_data="menu:logout")
    b.adjust(1)
    return b.as_markup()


def login_button() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔐 Iniciar sesión", callback_data="menu:login")
    return b.as_markup()


def yes_no_buttons() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Sí", callback_data="msg:btn_yes")
    b.button(text="❌ No", callback_data="msg:btn_no")
    return b.as_markup()
