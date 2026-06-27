import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "sessions.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "Falta BOT_TOKEN. Copia .env.example a .env y pon el token de @BotFather."
    )
