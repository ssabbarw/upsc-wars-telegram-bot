import asyncio
import logging
import os
from typing import Set

from telegram import Bot


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    token = "8443169277:AAFALqOURBJnMRaYTQ7lcK_mpp0FK3sHaq0"
    if not token:
        logger.error("TELEGRAM_TOKEN environment variable is not set.")
        print("Error: TELEGRAM_TOKEN environment variable is not set.")
        return

    logger.info("Using TELEGRAM_TOKEN starting with: %s...", token[:8])
    bot = Bot(token=token)

    logger.info(
        "Waiting for updates from Telegram. "
        "Send a message to your bot (or in any group where the bot is added)."
    )
    print(
        "Waiting for updates from Telegram...\n"
        "Send a message to your bot (or in any group where the bot is added), "
        "then wait a few seconds."
    )

    # Fetch recent updates and print unique chat IDs
    logger.info("Calling get_updates(timeout=60)...")
    updates = await bot.get_updates(timeout=60)
    logger.info("Received %d updates from Telegram.", len(updates))

    if not updates:
        logger.warning(
            "No updates received. Make sure you've just sent a message to the bot."
        )
        print("No updates received. Make sure you've sent a message to the bot.")
        return

    seen_chats: Set[int] = set()
    for update in updates:
        chat = update.effective_chat
        logger.debug("Raw update: %r", update)
        if not chat:
            continue
        if chat.id in seen_chats:
            continue
        seen_chats.add(chat.id)

        chat_type = chat.type
        title_or_name = (
            chat.title
            or chat.username
            or f"{chat.first_name or ''} {chat.last_name or ''}".strip()
        )
        msg = f"Chat ID: {chat.id} | Type: {chat_type} | Name/Title: {title_or_name}"
        logger.info(msg)
        print(msg)


if __name__ == "__main__":
    asyncio.run(main())
