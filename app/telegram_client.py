"""Thin async Telegram Bot API client using httpx."""

import httpx
from app.config import settings

BASE_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a text message to a Telegram chat."""
    async with httpx.AsyncClient() as client:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        resp = await client.post(f"{BASE_URL}/sendMessage", json=payload)
        return resp.json()


async def send_typing_action(chat_id: int) -> None:
    """Send a 'typing...' indicator to the chat."""
    async with httpx.AsyncClient() as client:
        payload = {"chat_id": chat_id, "action": "typing"}
        await client.post(f"{BASE_URL}/sendChatAction", json=payload)


async def get_file(file_id: str) -> dict:
    """Get file path from Telegram servers."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/getFile", params={"file_id": file_id})
        data = resp.json()
        return data.get("result", {})


async def download_file(file_path: str) -> bytes:
    """Download a file from Telegram servers."""
    url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.content


async def set_webhook(url: str) -> dict:
    """Set the Telegram webhook URL."""
    async with httpx.AsyncClient() as client:
        payload = {"url": url, "allowed_updates": ["message"]}
        resp = await client.post(f"{BASE_URL}/setWebhook", json=payload)
        return resp.json()


async def delete_webhook() -> dict:
    """Remove the current webhook."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/deleteWebhook")
        return resp.json()
