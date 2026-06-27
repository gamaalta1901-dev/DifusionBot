# Bot listador de súper grupos

Bot de Telegram que inicia sesión en la cuenta de cada usuario (vía MTProto con
Telethon) y lista **todos los súper grupos** a los que pertenece.

El código OTP de login se ingresa con un **teclado numérico inline**, nunca como
mensaje de texto, para evitar que Telegram lo invalide automáticamente.

## Cómo funciona

1. El usuario abre el bot y pulsa **Iniciar sesión**.
2. El bot pide **API ID**, **API Hash** (de <https://my.telegram.org/auth?to=apps>)
   y el **número de teléfono**.
3. Telegram envía el OTP a la app. El usuario lo escribe en el **teclado inline**
   (botones, no texto).
4. Si la cuenta tiene verificación en dos pasos, el bot pide la contraseña y
   borra ese mensaje de inmediato.
5. La sesión queda guardada. Desde el menú puede **listar súper grupos** o
   **cerrar sesión** (revoca la sesión en Telegram).

## Instalación

```powershell
cd "C:\Users\jinet\Desktop\tg-supergroups-bot"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edita .env y pon tu BOT_TOKEN de @BotFather
python bot.py
```

## Comandos

- `/start` — menú principal o bienvenida.
- `/login` — iniciar el flujo de login manualmente.

## Notas de seguridad

- `sessions.db` guarda el **API Hash** y la **StringSession** de cada usuario en
  texto plano. Es material sensible: protégelo y nunca lo subas a un repositorio
  (ya está en `.gitignore`).
- La StringSession da acceso completo a la cuenta del usuario. Trátala como una
  contraseña.
- El bot pide a los usuarios sus credenciales de API: úsalo solo con personas
  que confíen en ti.

## Estructura

| Archivo        | Función                                              |
|----------------|------------------------------------------------------|
| `bot.py`       | Arranque, dispatcher y routers.                      |
| `config.py`    | Carga de variables de entorno.                       |
| `storage.py`   | Persistencia de sesiones en SQLite.                  |
| `states.py`    | Estados del FSM del login.                           |
| `keyboards.py` | Teclado numérico y menús inline.                     |
| `auth.py`      | Flujo de login + OTP por teclado + 2FA.              |
| `groups.py`    | Listado de súper grupos y cierre de sesión.          |
| `runtime.py`   | Clientes Telethon en proceso de login (en memoria).  |
```
