import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from quiz_engine import QuizManager


logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
QUIZ_TIME_IST = time(21, 0, tzinfo=IST)  # 9:00 PM IST


def setup_daily_quiz_scheduler(application: Application, quiz_manager: QuizManager) -> None:
    """
    Configure the JobQueue to run the quiz daily at 9 PM IST and
    trigger immediately on startup if today's quiz was missed.
    """

    async def daily_quiz_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        now_ist = datetime.now(IST)
        today = now_ist.date()
        await quiz_manager.start_daily_quiz(context.application, today)

    async def startup_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        now_ist = datetime.now(IST)
        today = now_ist.date()

        # If we already ran today, nothing to do
        if quiz_manager.has_run_today(today):
            logger.info("Startup check: quiz already run today.")
            return

        # If current time is after the scheduled time, trigger immediately
        if now_ist.timetz() >= QUIZ_TIME_IST:
            logger.info(
                "Startup check: current time %s after %s; starting missed quiz.",
                now_ist.timetz(),
                QUIZ_TIME_IST,
            )
            await quiz_manager.start_daily_quiz(context.application, today)
        else:
            logger.info(
                "Startup check: current time %s before %s; quiz will run at scheduled time.",
                now_ist.timetz(),
                QUIZ_TIME_IST,
            )

    job_queue = application.job_queue

    # Schedule the daily quiz at 9 PM IST
    job_queue.run_daily(
        daily_quiz_job,
        time=QUIZ_TIME_IST,
        name="daily_quiz_9pm_ist",
    )

    # On startup, check whether today's quiz has already been missed
    job_queue.run_once(
        startup_check_job,
        when=0,
        name="startup_quiz_check",
    )

    logger.info("Daily quiz scheduler configured for 9 PM IST.")

