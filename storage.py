"""Persistencia en SQLite: sesiones de usuario y mensajes de difusión guardados."""

import aiosqlite

from config import DB_PATH

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id        INTEGER PRIMARY KEY,
    api_id         INTEGER NOT NULL,
    api_hash       TEXT    NOT NULL,
    phone          TEXT,
    session_string TEXT    NOT NULL,
    created_at     TEXT    DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_BROADCASTS = """
CREATE TABLE IF NOT EXISTS broadcasts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,   -- 'compose' | 'link'
    text         TEXT,               -- HTML del texto/caption
    media_path   TEXT,
    media_type   TEXT,               -- photo|video|audio|voice|document|...
    buttons_json TEXT,               -- filas de botones [{text,url}]
    links_json   TEXT,               -- lista de enlaces t.me
    created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_SCHEDULES = """
CREATE TABLE IF NOT EXISTS schedules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    broadcast_id     INTEGER NOT NULL,
    group_ids        TEXT    NOT NULL,   -- json: ids de grupos (snapshot)
    interval_seconds INTEGER NOT NULL,
    next_run         REAL    NOT NULL,   -- timestamp unix del próximo envío
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_SESSIONS)
        await db.execute(_CREATE_BROADCASTS)
        await db.execute(_CREATE_SCHEDULES)
        await db.commit()


# --------------------------------------------------------------------------- #
#  Sesiones                                                                    #
# --------------------------------------------------------------------------- #
async def save_session(
    user_id: int, api_id: int, api_hash: str, phone: str | None, session_string: str
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO sessions (user_id, api_id, api_hash, phone, session_string)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                api_id         = excluded.api_id,
                api_hash       = excluded.api_hash,
                phone          = excluded.phone,
                session_string = excluded.session_string
            """,
            (user_id, api_id, api_hash, phone, session_string),
        )
        await db.commit()


async def get_session(user_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def delete_session(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()


# --------------------------------------------------------------------------- #
#  Mensajes de difusión                                                        #
# --------------------------------------------------------------------------- #
async def add_broadcast(
    user_id: int,
    title: str,
    kind: str,
    text: str | None = None,
    media_path: str | None = None,
    media_type: str | None = None,
    buttons_json: str | None = None,
    links_json: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO broadcasts
                (user_id, title, kind, text, media_path, media_type, buttons_json, links_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, title, kind, text, media_path, media_type, buttons_json, links_json),
        )
        await db.commit()
        return cur.lastrowid


async def update_broadcast(
    broadcast_id: int,
    user_id: int,
    kind: str,
    text: str | None = None,
    media_path: str | None = None,
    media_type: str | None = None,
    buttons_json: str | None = None,
    links_json: str | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE broadcasts
               SET kind = ?, text = ?, media_path = ?, media_type = ?,
                   buttons_json = ?, links_json = ?
             WHERE id = ? AND user_id = ?
            """,
            (kind, text, media_path, media_type, buttons_json, links_json,
             broadcast_id, user_id),
        )
        await db.commit()


async def list_broadcasts(user_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title, kind FROM broadcasts WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        return await cur.fetchall()


async def get_broadcast(broadcast_id: int, user_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM broadcasts WHERE id = ? AND user_id = ?",
            (broadcast_id, user_id),
        )
        return await cur.fetchone()


async def delete_broadcast(broadcast_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM broadcasts WHERE id = ? AND user_id = ?",
            (broadcast_id, user_id),
        )
        await db.commit()


# --------------------------------------------------------------------------- #
#  Difusiones programadas                                                      #
# --------------------------------------------------------------------------- #
async def add_schedule(
    user_id: int,
    broadcast_id: int,
    group_ids_json: str,
    interval_seconds: int,
    next_run: float,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO schedules
                (user_id, broadcast_id, group_ids, interval_seconds, next_run)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, broadcast_id, group_ids_json, interval_seconds, next_run),
        )
        await db.commit()
        return cur.lastrowid


async def due_schedules(now: float) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM schedules WHERE active = 1 AND next_run <= ?", (now,)
        )
        return await cur.fetchall()


async def set_next_run(schedule_id: int, next_run: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE schedules SET next_run = ? WHERE id = ?", (next_run, schedule_id)
        )
        await db.commit()


async def list_schedules(user_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM schedules WHERE active = 1 AND user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        return await cur.fetchall()


async def get_schedule(schedule_id: int, user_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM schedules WHERE id = ? AND user_id = ? AND active = 1",
            (schedule_id, user_id),
        )
        return await cur.fetchone()


_SCHEDULE_UPDATABLE = ("group_ids", "broadcast_id", "interval_seconds", "next_run")


async def update_schedule(schedule_id: int, user_id: int, **fields) -> None:
    sets = {k: v for k, v in fields.items() if k in _SCHEDULE_UPDATABLE and v is not None}
    if not sets:
        return
    cols = ", ".join(f"{k} = ?" for k in sets)  # claves de allow-list, sin inyección
    vals = list(sets.values()) + [schedule_id, user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE schedules SET {cols} WHERE id = ? AND user_id = ?", vals
        )
        await db.commit()


async def delete_schedule(schedule_id: int, user_id: int | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if user_id is None:
            await db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        else:
            await db.execute(
                "DELETE FROM schedules WHERE id = ? AND user_id = ?",
                (schedule_id, user_id),
            )
        await db.commit()


async def delete_schedules_for_broadcast(user_id: int, broadcast_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM schedules WHERE user_id = ? AND broadcast_id = ?",
            (user_id, broadcast_id),
        )
        await db.commit()
