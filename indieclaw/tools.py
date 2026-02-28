from __future__ import annotations

import os
from pathlib import Path

import httpx

from . import workspace

MAX_TG_MSG = 4000


def _tg_api(method: str, *, timeout: int = 10, **kwargs) -> httpx.Response:
    """Call a Telegram Bot API method."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    with httpx.Client(timeout=timeout) as client:
        return client.post(f"https://api.telegram.org/bot{token}/{method}", **kwargs)


def _tg_api_md(method: str, body: dict, *, timeout: int = 10) -> httpx.Response:
    """Call a Telegram API method with Markdown, falling back to plain text."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"https://api.telegram.org/bot{token}/{method}", json={**body, "parse_mode": "Markdown"})
        if not r.is_success:
            r = client.post(f"https://api.telegram.org/bot{token}/{method}", json=body)
        return r


def _send_telegram(chat_id: str, message: str) -> str:
    try:
        chunks = [message[i:i + MAX_TG_MSG] for i in range(0, len(message), MAX_TG_MSG)]
        last_message_id = None
        for chunk in chunks:
            r = _tg_api_md("sendMessage", {"chat_id": chat_id, "text": chunk})
            if not r.is_success:
                if last_message_id is None:
                    return "Failed to send message."
                continue
            try:
                last_message_id = r.json().get("result", {}).get("message_id")
            except (ValueError, KeyError):
                pass
        if last_message_id:
            return f"Sent. [message_id={last_message_id}]"
        return "Sent."
    except Exception as e:
        return f"Error: {e}"


def _edit_telegram(chat_id: str, message_id: int, message: str) -> str:
    try:
        body = {"chat_id": chat_id, "message_id": message_id, "text": message[:MAX_TG_MSG]}
        r = _tg_api_md("editMessageText", body)
        return "Edited." if r.is_success else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_file(chat_id: str, file_path: str) -> str:
    try:
        path = Path(file_path).resolve()
        try:
            path.relative_to(workspace.HOME.resolve())
        except ValueError:
            return f"Error: file path {file_path!r} is outside the workspace."
        with open(path, "rb") as f:
            r = _tg_api("sendDocument", timeout=30, data={"chat_id": chat_id}, files={"document": (path.name, f)})
        return "Sent." if r.is_success else f"Failed: {r.text}"
    except FileNotFoundError:
        return f"File not found: {file_path}"
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_voice(chat_id: str, audio_path: str, caption: str = "") -> str:
    try:
        path = Path(audio_path).resolve()
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        with open(path, "rb") as f:
            r = _tg_api("sendVoice", timeout=30, data=data, files={"voice": (path.name, f, "audio/ogg")})
        return "Sent." if r.is_success else f"Failed: {r.text}"
    except FileNotFoundError:
        return f"File not found: {audio_path}"
    except Exception as e:
        return f"Error: {e}"


def _text_to_voice(text: str, output_path: str, voice: str = "en-US-AriaNeural") -> str:
    import subprocess
    import tempfile

    mp3_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            mp3_path = tmp.name

        result = subprocess.run(
            ["edge-tts", "--voice", voice, "--text", text, "--write-media", mp3_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"TTS failed: {result.stderr[:200]}"

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "48k", output_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"FFmpeg conversion failed: {result.stderr[:200]}"

        return output_path
    except Exception as e:
        return f"Error: {e}"
    finally:
        if mp3_path:
            Path(mp3_path).unlink(missing_ok=True)


def _set_reaction(chat_id: str, message_id: int, emoji: str) -> str:
    try:
        r = _tg_api("setMessageReaction", json={
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        })
        return "Done." if r.is_success else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"
