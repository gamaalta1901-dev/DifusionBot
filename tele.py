"""Utilidades compartidas para los clientes Telethon (sesión de usuario)."""

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel


def make_client(row) -> TelegramClient:
    return TelegramClient(
        StringSession(row["session_string"]), row["api_id"], row["api_hash"]
    )


async def open_client(row) -> TelegramClient:
    client = make_client(row)
    await client.connect()
    return client


async def fetch_supergroups(client: TelegramClient):
    """Devuelve (lista, entidades).

    lista     -> [(nombre, @username|None, id), ...]
    entidades -> {id: entity}  (para poder enviar mensajes después)

    Iterar los diálogos también deja en caché las entidades de la sesión, lo
    que permite resolver enlaces privados al difundir.
    """
    grupos: list[tuple[str, str | None, int]] = []
    entidades: dict[int, Channel] = {}
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        # Súper grupo = Channel con megagroup=True (excluye canales broadcast
        # y grupos básicos antiguos).
        if isinstance(ent, Channel) and getattr(ent, "megagroup", False):
            username = f"@{ent.username}" if ent.username else None
            grupos.append((dialog.name or "—", username, ent.id))
            entidades[ent.id] = ent
    return grupos, entidades
