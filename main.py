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

# Tokenni to'g'ri olish (os.getenv da ichiga to'g'ridan-to'g'ri token yozilmaydi)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8607280759:AAFJCl2KGPJsfhAmh6dbNb3ijAKxhI4bgRA")
MAX_SIZE = 50 * 1024 * 1024

# Cookies faylidan foydalanish uchun (agar kerak bo'lmasa None qoldirasiz)
COOKIES_FILE = os.getenv("COOKIES_FILE", None)

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
        await status.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dp.message()
async def other(message: Message):
    await message.answer("Instagram yoki YouTube link yuboring.")


async def main():
    bot = Bot(BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())