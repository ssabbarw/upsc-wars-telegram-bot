import os
import asyncio

from telegram import Bot


async def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("GROUP_CHAT_ID")

    if not token or not chat_id:
        print("Set TELEGRAM_TOKEN and GROUP_CHAT_ID environment variables first.")
        return

    # Debug logging (be careful in production: this exposes secrets)
    masked_token = token[:4] + "..." + token[-4:] if len(token) >= 8 else token
    print(f"Using TELEGRAM_TOKEN={masked_token}, GROUP_CHAT_ID={chat_id!r}")

    bot = Bot(token=token)
    text = "Will share the result soon!!"

    await bot.send_message(chat_id=int(chat_id), text=text)
    print(f"Sent test message to chat {chat_id!r}.")


if __name__ == "__main__":
    asyncio.run(main())

