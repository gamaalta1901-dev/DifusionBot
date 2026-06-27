"""Planificador en segundo plano para las difusiones programadas.

Cada POLL segundos revisa la tabla `schedules` y ejecuta las que ya vencieron.
Es persistente: al reiniciar, retoma las programaciones desde la base de datos.
"""

import asyncio
import json
import logging
import time

import broadcast as bc_mod
import storage

log = logging.getLogger(__name__)

POLL = 30  # segundos entre revisiones


async def run_scheduler(bot) -> None:
    while True:
        try:
            for sch in await storage.due_schedules(time.time()):
                await _run_one(bot, sch)
        except Exception:
            log.exception("error en el bucle del planificador")
        await asyncio.sleep(POLL)


async def _run_one(bot, sch) -> None:
    user_id = sch["user_id"]
    row = await storage.get_session(user_id)
    bc = await storage.get_broadcast(sch["broadcast_id"], user_id)
    if not row or not bc:
        # sesión cerrada o mensaje borrado -> cancelar la programación
        await storage.delete_schedule(sch["id"])
        return

    # Reservamos el próximo turno antes de enviar, para no re-disparar si tarda.
    await storage.set_next_run(sch["id"], time.time() + sch["interval_seconds"])

    group_ids = json.loads(sch["group_ids"])
    hrs = sch["interval_seconds"] // 3600
    try:
        ok, fail, reasons = await bc_mod.broadcast_message(row, bc, group_ids)
    except bc_mod.SessionInvalid:
        await storage.delete_session(user_id)
        await storage.delete_schedule(sch["id"])
        await _notify(bot, user_id,
                      "⚠️ Tu sesión expiró; cancelé la difusión programada. Reinicia con /start.")
        return
    except Exception as e:
        log.warning("difusión programada falló (id=%s): %s", sch["id"], e)
        return

    await _notify(
        bot, user_id,
        f"⏱ Difusión «{bc['title']}» enviada a {ok}/{len(group_ids)} grupo(s) "
        f"(fallidos: {fail}). Siguiente en {hrs}h." + bc_mod._reasons_text(reasons),
    )


async def _notify(bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception:
        pass
