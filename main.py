import logging
import os
import sys
from datetime import datetime

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, PollAnswerHandler

from question_loader import load_and_preprocess_questions
from quiz_engine import (
    QUIZ_INTER_QUESTION_DELAY_SECONDS,
    QUIZ_POLL_ANSWER_BUFFER_SECONDS,
    QUIZ_POLL_DURATION_SECONDS,
    QUIZ_QUESTION_COUNT,
    QuizManager,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    quiz_manager: QuizManager | None = context.application.bot_data.get("quiz_manager")
    if not quiz_manager:
        logger.warning("Received poll answer but QuizManager is not configured.")
        return

    await quiz_manager.handle_poll_answer(update, context)


def get_env_var(name: str) -> str:
    """
    Helper to read required environment variables, exiting if missing.
    """
    value = os.environ.get(name)
    if not value:
        logger.error("Environment variable %s is required but not set.", name)
        sys.exit(1)
    return value


def build_application() -> Application:
    token = get_env_var("TELEGRAM_TOKEN")
    group_chat_id_raw = get_env_var("GROUP_CHAT_ID")
    admin_chat_id_raw = os.environ.get("ADMIN_CHAT_ID")

    try:
        group_chat_id = int(group_chat_id_raw)
    except ValueError:
        logger.error("GROUP_CHAT_ID must be an integer, got %s", group_chat_id_raw)
        sys.exit(1)

    admin_chat_id: int | None = None
    if admin_chat_id_raw:
        try:
            admin_chat_id = int(admin_chat_id_raw)
        except ValueError:
            logger.error("ADMIN_CHAT_ID must be an integer, got %s", admin_chat_id_raw)

    logger.info(
        "Effective config -> GROUP_CHAT_ID=%s, ADMIN_CHAT_ID=%s, QUIZ_QUESTION_COUNT=%s, "
        "QUIZ_POLL_DURATION_SECONDS=%s, QUIZ_POLL_ANSWER_BUFFER_SECONDS=%s, "
        "QUIZ_INTER_QUESTION_DELAY_SECONDS=%s",
        group_chat_id,
        admin_chat_id,
        QUIZ_QUESTION_COUNT,
        QUIZ_POLL_DURATION_SECONDS,
        QUIZ_POLL_ANSWER_BUFFER_SECONDS,
        QUIZ_INTER_QUESTION_DELAY_SECONDS,
    )

    # Load and preprocess questions on every startup
    questions = load_and_preprocess_questions()
    logger.info("Loaded %d preprocessed questions.", len(questions))

    quiz_manager = QuizManager(
        all_questions=questions,
        group_chat_id=group_chat_id,
        admin_chat_id=admin_chat_id,
    )
    # post_init callback to start the quiz as soon as the bot is fully initialized
    async def startup_quiz(application: Application) -> None:
        logger.info("Starting manual quiz run immediately after initialization.")

        # Announce quiz start in the target group with today's date
        today_str = datetime.now().strftime("%d %b %Y")
        start_text = f"*Starting Quiz - {today_str}*\nGet ready for today's questions!"
        await application.bot.send_message(
            chat_id=group_chat_id,
            text=start_text,
            parse_mode="Markdown",
        )

        # If ADMIN_CHAT_ID is configured, send an admin notification as well.
        if admin_chat_id is not None:
            admin_text = (
                f"🚀 Starting quiz for group `{group_chat_id}` on {today_str}.\n"
                f"Questions: {QUIZ_QUESTION_COUNT}, "
                f"poll duration: {QUIZ_POLL_DURATION_SECONDS}s."
            )
            await application.bot.send_message(
                chat_id=admin_chat_id,
                text=admin_text,
                parse_mode="Markdown",
            )

        await quiz_manager.start_quiz_now(application)

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(startup_quiz)
        .build()
    )

    application.bot_data["quiz_manager"] = quiz_manager

    # Handlers
    application.add_handler(PollAnswerHandler(handle_poll_answer))

    return application


def main() -> None:
    application = build_application()

    logger.info("Starting bot with polling...")
    application.run_polling(
        allowed_updates=["poll_answer"],
        close_loop=False,
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")

