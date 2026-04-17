"""
test_async_send.py — verifies the dedicated-loop pattern works from background threads.

The fix: spin a dedicated asyncio loop in a daemon thread, start Telethon on it,
then run_coroutine_threadsafe from any thread. Loop is always running → futures
always complete.

Run: /Users/ssr/Documents/algo/venv/bin/python test_async_send.py
Expect 3 messages in qwerty channel.
"""

import asyncio
import threading
import time
import logging

from telethon import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

api_id   = "24665115"
api_hash = "4bb48e7b1dd0fcb763dfe9eb203a6216"
bot_tokens = [
    "6476234483:AAHoQHtdKv8aHfmUgJG14ETxsZnqXjtutwU",
    "6412363785:AAE7ULHsAj1Y77ctZCGy_4t06zQVJgkMo8I",
]
qwerty_channel = -1001767848638

# --- dedicated event loop running in a background thread ---
_tg_loop = asyncio.new_event_loop()

def _run_loop():
    asyncio.set_event_loop(_tg_loop)
    _tg_loop.run_forever()

threading.Thread(target=_run_loop, name="tg-loop", daemon=True).start()

# Create client on the dedicated loop
client = TelegramClient("/tmp/test_async_send_session", api_id, api_hash, loop=_tg_loop)


async def send_message(text):
    await client.send_message(qwerty_channel, f"```[test_async_send]\n\n{text}```", parse_mode="md")


def _send_message_sync(text):
    """Submit to dedicated loop — safe from any thread."""
    future = asyncio.run_coroutine_threadsafe(send_message(text), _tg_loop)
    try:
        future.result(timeout=10)
        logger.info(f"  [OK] sent: {text!r}")
    except Exception as e:
        logger.error(f"  [FAIL] {type(e).__name__}: {e!r}")


def background_thread():
    logger.info("Background thread starting (simulates PriceDispatcher)...")
    _send_message_sync("Test 1/3 — background thread send (simulates on_price)")
    time.sleep(0.5)
    _send_message_sync("Test 2/3 — second call from background thread")
    time.sleep(0.5)
    _send_message_sync("Test 3/3 — dedicated-loop pattern confirmed ✅")
    logger.info("Background thread done.")


# ---------- main ----------

# Start Telethon on the dedicated loop
async def _start():
    for token in bot_tokens:
        try:
            await client.start(bot_token=token)
            logger.info(f"Connected with token ...{token[-10:]}")
            return True
        except Exception as e:
            logger.warning(f"Token failed: {e}")
    return False

connected = asyncio.run_coroutine_threadsafe(_start(), _tg_loop).result(timeout=30)
if not connected:
    logger.error("Could not connect. Aborting.")
    exit(1)

logger.info(f"_tg_loop running: {_tg_loop.is_running()}")

t = threading.Thread(target=background_thread)
t.start()
t.join(timeout=20)

logger.info("Test complete — check qwerty for 3 messages.")
asyncio.run_coroutine_threadsafe(client.disconnect(), _tg_loop).result(timeout=10)
_tg_loop.call_soon_threadsafe(_tg_loop.stop)
