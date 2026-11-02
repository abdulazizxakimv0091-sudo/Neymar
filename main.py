import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN", "8607280759:AAFJCl2KGPJsfhAmh6dbNb3ijAKxhI4bgRA")
PORT = int(os.getenv("PORT", "10000"))
MAX_SIZE = 50 * 1024 * 1024  # Telegram bot limiti: 50 MB

# YouTube uchun ixtiyoriy cookies fayli (Render Secret File yoki yo'l)
COOKIES_FILE = os.getenv("COOKIES_FILE")

URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:instagram\.com|youtube\.com|youtu\.be)/\S+"
)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


def download(url: str, out_dir: str) -> Path:
    opts = {
        "outtmpl": f"{out_dir}/%(id)s.%(ext)s",
        "format": "b[ext=mp4][filesize<?50M]/b[filesize<?50M]/b",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "max_filesize": MAX_SIZE,
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [p for p in Path(out_dir).iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("Fayl yuklanmadi")
    return max(files, key=lambda p: p.stat().st_size)


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Salom! Instagram yoki YouTube link yuboring, men videoni yuklab beraman."
    )


@dp.message(F.text.regexp(URL_RE))
async def handle_link(message: Message):
    match = URL_RE.search(message.text)
    if not match:
        return
    url = match.group(0)

    status = await message.answer("⏳ Yuklanmoqda...")
    tmp = tempfile.mkdtemp()

    try:
        path = await asyncio.to_thread(download, url, tmp)

        if path.stat().st_size > MAX_SIZE:
            await status.edit_text("❌ Fayl 50 MB dan katta, yubora olmayman.")
            return

        file = FSInputFile(path)
        if path.suffix.lower() in IMAGE_EXT:
            await message.answer_photo(file)
        else:
            await message.answer_video(file, supports_streaming=True)
        await status.delete()

    except Exception as e:
        logging.exception("Download error")
        text = str(e)
        if "Sign in to confirm" in text or "429" in text:
            text = "YouTube serverni blokladi. Keyinroq urinib ko'ring yoki Instagram link yuboring."
        await status.edit_text(f"❌ Xatolik: {text[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dp.message()
async def other(message: Message):
    await message.answer("Instagram yoki YouTube link yuboring.")


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_web_server() -> web.AppRunner:
    """Render Web Service port kutadi, shuning uchun kichik server ochamiz."""
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Health server port %s da ishlamoqda", PORT)
    return runner


async def main():
    runner = await start_web_server()
    bot = Bot(BOT_TOKEN)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())