from aiogram.fsm.state import State, StatesGroup


class Auth(StatesGroup):
    api_id = State()
    api_hash = State()
    phone = State()
    code = State()      # se ingresa con el teclado numérico inline
    password = State()  # solo si la cuenta tiene verificación en dos pasos


class MsgSave(StatesGroup):
    # Flujo "Mensaje nuevo"
    new_wait_message = State()
    new_ask_buttons = State()
    new_wait_buttons = State()
    new_wait_title = State()
    # Flujo "Enviar Enlace"
    link_wait_links = State()
    link_wait_title = State()
