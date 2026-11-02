import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import web

# Videodagi musiqani aniqlash (Shazam). O'rnatilmagan bo'lsa bot baribir ishlaydi.
try:
    import imageio_ffmpeg
    from shazamio import Shazam

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    Shazam = None
    FFMPEG = shutil.which("ffmpeg")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8607280759:AAFJCl2KGPJsfhAmh6dbNb3ijAKxhI4bgRA")
PORT = int(os.getenv("PORT", "10000"))
MAX_SIZE = 50 * 1024 * 1024  # Telegram bot limiti: 50 MB
SEARCH_COUNT = 8

# Instagram va SoundCloud linklari
URL_RE = re.compile(
    r"https?://(?:www\.|m\.|on\.)?(?:instagram\.com|soundcloud\.com)/\S+"
)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

# Qidiruv natijalari xotirada: {kalit: [ {url,title,duration}, ... ]}
SEARCH_CACHE: dict[str, list[dict]] = {}


def fmt_duration(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def search_music(query: str) -> list[dict]:
    opts = {"quiet": True, "noprogress": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{SEARCH_COUNT}:{query}", download=False)
    results = []
    for e in info.get("entries", []) or []:
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        results.append(
            {
                "url": url,
                "title": e.get("title") or "Nomsiz",
                "duration": e.get("duration"),
                "uploader": e.get("uploader") or "",
            }
        )
    return results


def download(url: str, out_dir: str, audio: bool = False) -> Path:
    opts = {
        "outtmpl": f"{out_dir}/%(id)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "max_filesize": MAX_SIZE,
    }
    if audio:
        # ffmpeg shart emas: tayyor mp3/m4a formatni olamiz
        opts["format"] = "bestaudio[ext=mp3]/http_mp3_128/bestaudio/best"
    else:
        opts["format"] = "b[ext=mp4][filesize<?50M]/b[filesize<?50M]/b"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [p for p in Path(out_dir).iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("Fayl yuklanmadi")
    return max(files, key=lambda p: p.stat().st_size)


def extract_audio(src: str, dst: str, start: int) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(start), "-i", src, "-t", "20",
         "-vn", "-ac", "1", "-ar", "44100", dst],
        check=True, capture_output=True, timeout=90,
    )
    if not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        raise RuntimeError("Audio topilmadi")


async def identify(src: str):
    """Video/audio ichidagi musiqani aniqlaydi. {'title','artist'} yoki None."""
    if Shazam is None or not FFMPEG:
        return None
    shazam = Shazam()
    for start in (0, 20):
        wav = f"{src}.{start}.wav"
        try:
            await asyncio.to_thread(extract_audio, src, wav, start)
            out = await shazam.recognize(wav)
        except Exception:
            logging.exception("Identify error")
            continue
        track = out.get("track")
        if track:
            return {"title": track.get("title", ""), "artist": track.get("subtitle", "")}
    return None


async def announce_song(message: Message, path) -> None:
    st = await message.answer("🎧 Videodagi musiqa aniqlanmoqda...")
    track = await identify(str(path))
    if not track:
        await st.edit_text("🤷 Videodagi musiqani aniqlay olmadim.")
        return
    await st.edit_text(f"🎵 {track['artist']} — {track['title']}")
    await send_search(message, f"{track['artist']} {track['title']}")


async def send_search(message: Message, query: str) -> None:
    status = await message.answer("🔎 Qidirilmoqda...")
    try:
        results = await asyncio.to_thread(search_music, query)
    except Exception as e:
        logging.exception("Search error")
        await status.edit_text(f"❌ Qidirishda xatolik: {str(e)[:200]}")
        return

    if not results:
        await status.edit_text("😕 Hech narsa topilmadi. Boshqacha yozib ko'ring.")
        return

    key = f"{message.chat.id}{int(time.time()) % 100000}"
    SEARCH_CACHE[key] = results
    if len(SEARCH_CACHE) > 200:  # xotira to'lib ketmasligi uchun
        SEARCH_CACHE.pop(next(iter(SEARCH_CACHE)))

    rows = []
    for i, r in enumerate(results):
        dur = fmt_duration(r["duration"])
        label = f"{i + 1}. {r['title']}"[:50] + (f" ({dur})" if dur else "")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"m:{key}:{i}")])

    await status.edit_text(
        "Topildi, tanlang 👇", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Salom! 🎵 Qo'shiq yoki ijrochi nomini yozing, men musiqa topib beraman.\n"
        "Instagram link yuborsangiz, videoni yuklab beraman va ichidagi musiqani topaman.\n"
        "Video yoki ovozli xabar yuborsangiz ham musiqasini aniqlayman."
    )


@dp.message(F.text.regexp(URL_RE))
async def handle_link(message: Message):
    match = URL_RE.search(message.text)
    if not match:
        return
    url = match.group(0)
    is_music = "soundcloud.com" in url

    status = await message.answer("⏳ Yuklanmoqda...")
    tmp = tempfile.mkdtemp()
    try:
        path = await asyncio.to_thread(download, url, tmp, is_music)

        if path.stat().st_size > MAX_SIZE:
            await status.edit_text("❌ Fayl 50 MB dan katta, yubora olmayman.")
            return

        file = FSInputFile(path)
        if path.suffix.lower() in IMAGE_EXT:
            await message.answer_photo(file)
        elif is_music:
            await message.answer_audio(file)
        else:
            await message.answer_video(file, supports_streaming=True)
        await status.delete()
        if not is_music and path.suffix.lower() not in IMAGE_EXT:
            await announce_song(message, path)
    except Exception as e:
        logging.exception("Download error")
        await status.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dp.message(F.video | F.video_note | F.audio | F.voice | F.animation)
async def handle_media(message: Message):
    media = (message.video or message.video_note or message.audio
             or message.voice or message.animation)
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "input")
        try:
            await message.bot.download(media, destination=path)
        except Exception:
            await message.answer("❌ Fayl juda katta (Telegram botlar uchun 20 MB gacha).")
            return
        await announce_song(message, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_search(message: Message):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Qo'shiq nomini yozing.")
        return
    await send_search(message, query)


@dp.callback_query(F.data.startswith("m:"))
async def pick_track(cb: CallbackQuery):
    _, key, idx = cb.data.split(":")
    results = SEARCH_CACHE.get(key)
    if not results:
        await cb.answer("Qidiruv eskirgan, qaytadan yozing.", show_alert=True)
        return
    track = results[int(idx)]
    await cb.answer("Yuklanmoqda...")
    status = await cb.message.answer(f"⏳ {track['title'][:60]}")

    tmp = tempfile.mkdtemp()
    try:
        path = await asyncio.to_thread(download, track["url"], tmp, True)
        if path.stat().st_size > MAX_SIZE:
            await status.edit_text("❌ Fayl 50 MB dan katta.")
            return
        await cb.message.answer_audio(
            FSInputFile(path),
            title=track["title"][:60],
            performer=track["uploader"][:60] or None,
        )
        await status.delete()
    except Exception as e:
        logging.exception("Track download error")
        await status.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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