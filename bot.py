import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import storage
from auth import router as auth_router
from broadcast import router as broadcast_router
from config import BOT_TOKEN
from groups import router as groups_router
from messages import router as messages_router
from scheduler import run_scheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Silenciar el ruido INFO de las librerías; solo avisos y errores.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    await storage.init_db()

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    dp = Dispatcher()
    # auth va al final: su fallback de mensajes captura todo lo no manejado.
    dp.include_router(messages_router)
    dp.include_router(broadcast_router)
    dp.include_router(groups_router)
    dp.include_router(auth_router)

    scheduler_task = asyncio.create_task(run_scheduler(bot))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
