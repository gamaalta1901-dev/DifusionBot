"""Estado en memoria compartido entre handlers (se pierde al reiniciar)."""

from telethon import TelegramClient

# user_id -> TelegramClient (solo durante el login)
pending_clients: dict[int, TelegramClient] = {}

# user_id -> lista de (nombre, @username|None, id) de sus súper grupos
group_cache: dict[int, list[tuple[str, str | None, int]]] = {}

# user_id -> conjunto de ids de grupos seleccionados para difundir
selected: dict[int, set[int]] = {}

# user_id -> id de la difusión programada cuyos grupos se están editando
# (cuando está presente, el selector de grupos entra en "modo edición")
editing_schedule: dict[int, int] = {}
