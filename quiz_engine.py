import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from telegram import Poll, Update
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes


logger = logging.getLogger(__name__)
LOG_MARKER = "***$$&&***"


# Core quiz configuration (tweak these before running main.py)
# Total number of questions to ask per quiz run.
QUIZ_QUESTION_COUNT = 10

# Explanation mode: "final" uses solution.final_explanation, "analysis" uses
# solution.statement_analysis (if present), falling back to final_explanation.
EXPLANATION_MODE = "analysis"  # or "final"

# Timing configuration (tweak these constants before running main.py)
# How long each poll stays open for users to answer (seconds).
QUIZ_POLL_DURATION_SECONDS = 50
# Extra safety buffer after the poll closes so all PollAnswer updates are processed
# before computing winners (seconds).
QUIZ_POLL_ANSWER_BUFFER_SECONDS = 5
# Wait time between announcing results for one question and posting the next (seconds).
QUIZ_INTER_QUESTION_DELAY_SECONDS = 10


@dataclass
class QuizQuestion:
    id: str
    question_text: str
    correct_index: int  # 0-based index for A/B/C/D
    explanation: str
    year: int | None = None
    # Optional: separated options text (without labels), if we could parse them.
    options: Optional[List[str]] = None
    # Optional: structured statement analysis entries.
    statement_analysis: Optional[List[Dict[str, str]]] = None
    # Optional: elimination logic bullet points.
    elimination_logic: Optional[List[str]] = None


@dataclass
class QuizSession:
    questions: List[QuizQuestion]
    # poll_id -> index in questions
    poll_to_question_index: Dict[str, int] = field(default_factory=dict)
    # question index -> user_id -> chosen option index
    answers: Dict[int, Dict[int, int]] = field(default_factory=dict)
    # user_id -> display name (e.g., @username or "First Last")
    usernames: Dict[int, str] = field(default_factory=dict)
    # Indices of questions that were successfully sent (question text + poll).
    asked_question_indices: List[int] = field(default_factory=list)
    # Question index -> datetime when its poll was sent (used for timing).
    question_start_times: Dict[int, datetime] = field(default_factory=dict)
    # Question index -> user_id -> time taken in seconds for final answer.
    answer_times: Dict[int, Dict[int, float]] = field(default_factory=dict)


class QuizManager:
    def __init__(
        self,
        all_questions: List[dict],
        group_chat_id: int,
        admin_chat_id: Optional[int] = None,
    ) -> None:
        if len(all_questions) < QUIZ_QUESTION_COUNT:
            raise ValueError(
                f"QuizManager requires at least {QUIZ_QUESTION_COUNT} questions."
            )

        self._all_questions: List[QuizQuestion] = [
            QuizQuestion(
                id=q["id"],
                question_text=q["question_text"],
                correct_index=int(q["correct_index"]),
                explanation=q.get("explanation", ""),
                year=q.get("year"),
                options=q.get("options"),
                statement_analysis=q.get("statement_analysis"),
                elimination_logic=q.get("elimination_logic"),
            )
            for q in all_questions
        ]
        self.group_chat_id = group_chat_id
        self.admin_chat_id = admin_chat_id

        self._lock = asyncio.Lock()
        self._active_session: Optional[QuizSession] = None
        # We intentionally do not track last run date in this MVP; every time the
        # process starts, a new quiz run is triggered via start_quiz_now.

    async def start_quiz_now(self, application: Application) -> None:
        """
        Start a quiz immediately, regardless of date, if one is not already running.
        """
        async with self._lock:
            if self._active_session is not None:
                logger.warning("A quiz session is already active. Skipping new start.")
                return

            selected = random.sample(self._all_questions, QUIZ_QUESTION_COUNT)
            self._active_session = QuizSession(questions=selected)
            logger.info(
                "%s Starting manual quiz with %d questions.",
                LOG_MARKER,
                QUIZ_QUESTION_COUNT,
            )

            asyncio.create_task(self._run_quiz(application))

    async def _run_quiz(self, application: Application) -> None:
        assert self._active_session is not None
        session = self._active_session

        try:
            for idx, question in enumerate(session.questions):
                q_number = idx + 1

                try:
                    # Build a full question text message (no 300-char limit here).
                    # First line: question number; second: PYQ year (if available);
                    # then a blank line and the question text. If we have parsed
                    # options and are showing them in the poll (short enough), we
                    # strip the trailing (a)...(d) options from this text to avoid
                    # duplication. Otherwise we keep options in the stem so users
                    # can still read them when the poll shows only A/B/C/D.
                    full_text = question.question_text.strip()
                    full_options_ok = (
                        question.options
                        and len(question.options) == 4
                        and all(1 <= len(opt) <= 100 for opt in question.options)
                    )
                    stem = full_text
                    if full_options_ok:
                        m = re.search(r"\([a-dA-D]\)", full_text)
                        if m:
                            stem = full_text[: m.start()].rstrip()

                    lines: List[str] = [f"*Question* #{q_number}"]
                    if question.year is not None:
                        lines.append(f"*PYQ Year:* {question.year}")
                    lines.append("")
                    lines.append(stem)

                    full_question_text = "\n".join(lines)

                    logger.info(
                        "%s Posting question %d/%d (id=%s) to chat %s",
                        LOG_MARKER,
                        q_number,
                        len(session.questions),
                        question.id,
                        self.group_chat_id,
                    )

                    # Send full question as a normal message
                    await application.bot.send_message(
                        chat_id=self.group_chat_id,
                        text=full_question_text,
                        parse_mode="Markdown",
                    )

                    # Use a very short poll question to avoid the 300-char limit
                    poll_question = f"Q{q_number}"

                    # Decide whether to show full options in the poll or fallback to A/B/C/D
                    poll_options: List[str]
                    if full_options_ok:
                        # Use full option text directly in the poll (no (a)/(b) labels)
                        poll_options = question.options
                        logger.info(
                            "%s Using full option text for Q%d in poll.",
                            LOG_MARKER,
                            q_number,
                        )
                    else:
                        # Use compact poll options (A, B, C, D); actual option text is
                        # already included in the question statement message above.
                        poll_options = ["A", "B", "C", "D"]
                        logger.info(
                            "%s Using A/B/C/D options for Q%d in poll (full options too long or unavailable).",
                            LOG_MARKER,
                            q_number,
                        )

                    # Pre-log correct answer so you can test right/wrong from another account
                    correct_letter = chr(ord("A") + question.correct_index)
                    logger.info(
                        "%s Correct option for Q%d is %s (index=%d)",
                        LOG_MARKER,
                        q_number,
                        correct_letter,
                        question.correct_index,
                    )

                    # Record question start time just before sending the poll
                    question_start_time = datetime.now()
                    message = await application.bot.send_poll(
                        chat_id=self.group_chat_id,
                        question=poll_question,
                        options=poll_options,
                        type=Poll.QUIZ,
                        correct_option_id=question.correct_index,
                        is_anonymous=False,
                        open_period=QUIZ_POLL_DURATION_SECONDS,
                    )

                    poll_id = message.poll.id
                    session.poll_to_question_index[poll_id] = idx
                    session.asked_question_indices.append(idx)
                    session.question_start_times[idx] = question_start_time
                    logger.info(
                        "%s Question %d poll created with poll_id=%s, waiting %s+%s seconds for answers.",
                        LOG_MARKER,
                        q_number,
                        poll_id,
                        QUIZ_POLL_DURATION_SECONDS,
                        QUIZ_POLL_ANSWER_BUFFER_SECONDS,
                    )

                    # Wait for poll to close plus a configurable buffer
                    await asyncio.sleep(
                        QUIZ_POLL_DURATION_SECONDS + QUIZ_POLL_ANSWER_BUFFER_SECONDS
                    )

                    # Compute correct users for this question
                    correct_users = self._get_correct_users_for_question(
                        idx, question.correct_index
                    )

                    correct_letter = chr(ord("A") + question.correct_index)
                    logger.info(
                        "%s Question %d finished. Correct option=%s. Correct users count=%d.",
                        LOG_MARKER,
                        q_number,
                        correct_letter,
                        len(correct_users),
                    )

                    # Per-question explanation (no need to repeat statement)
                    if EXPLANATION_MODE == "analysis" and question.statement_analysis:
                        # Build explanation text from structured statement analysis.
                        analysis_lines: List[str] = []
                        for i, entry in enumerate(question.statement_analysis):
                            # Divider line before each statement block to make it clearer.
                            analysis_lines.append("----------")
                            stmt = entry.get("statement", "")
                            verdict = entry.get("verdict", "")
                            reason = entry.get("reason", "")
                            if stmt:
                                analysis_lines.append(f"- *Statement:* {stmt}")
                            if verdict:
                                analysis_lines.append(f"  *Verdict:* {verdict}")
                            if reason:
                                analysis_lines.append(f"  {reason}")
                            analysis_lines.append("")  # blank line between entries

                        # Optionally append elimination logic section if present
                        if question.elimination_logic:
                            analysis_lines.append("🔍 *Elimination logic:*")
                            for point in question.elimination_logic:
                                analysis_lines.append(f"- {point}")

                        explanation_body = (
                            "\n".join(analysis_lines).rstrip()
                            or "No explanation provided."
                        )
                    else:
                        explanation_body = (
                            question.explanation or "No explanation provided."
                        )

                    explanation_msg = (
                        f"💡 *Explanation for Q{q_number}:*\n"
                        f"*Correct answer: {correct_letter}*\n\n{explanation_body}"
                    )
                    await application.bot.send_message(
                        chat_id=self.group_chat_id,
                        text=explanation_msg,
                        parse_mode="Markdown",
                    )

                    # Build per-question results with time taken for each correct user.
                    answers_for_question = session.answers.get(idx, {})
                    answer_times_for_question = session.answer_times.get(idx, {})
                    correct_entries: List[tuple[float, str]] = []
                    for user_id, chosen_index in answers_for_question.items():
                        if chosen_index != question.correct_index:
                            continue
                        display_name = session.usernames.get(
                            user_id, f"user_id:{user_id}"
                        )
                        elapsed = answer_times_for_question.get(user_id)
                        if elapsed is None:
                            entry_text = f"{display_name}, time: N/A"
                            sort_key = float("inf")
                        else:
                            seconds = int(round(elapsed))
                            entry_text = f"{display_name}, in {seconds} seconds"
                            sort_key = elapsed
                        correct_entries.append((sort_key, entry_text))

                    correct_entries.sort(key=lambda item: item[0])

                    if correct_entries:
                        users_list = "\n".join(entry for _, entry in correct_entries)
                    else:
                        users_list = "None"

                    # Per-question result: who got it right (correct answer is already in explanation)
                    announce_text = (
                        f"✅ Q{q_number}\nCorrect answer by:\n{users_list}"
                    )

                    await application.bot.send_message(
                        chat_id=self.group_chat_id,
                        text=announce_text,
                    )

                    # If this is not the last question, inform users when the next
                    # question will appear. First send a text message, then a simple
                    # regular poll so Telegram shows its timer UI.
                    if q_number < len(session.questions):
                        next_q_text = (
                            f"⏭ Next question coming up in few seconds..."
                        )
                        await application.bot.send_message(
                            chat_id=self.group_chat_id,
                            text=next_q_text,
                            parse_mode="Markdown",
                        )

                        logger.info(
                            "%s Waiting %s seconds before posting next question.",
                            LOG_MARKER,
                            QUIZ_INTER_QUESTION_DELAY_SECONDS,
                        )
                        # Wait before next question
                        await asyncio.sleep(QUIZ_INTER_QUESTION_DELAY_SECONDS)

                except BadRequest as exc:
                    logger.error(
                        "%s Failed to send question %d (id=%s) due to BadRequest: %s. "
                        "Skipping this question and continuing.",
                        LOG_MARKER,
                        q_number,
                        question.id,
                        exc,
                    )
                    # Notify admin (if configured) about the failure.
                    if self.admin_chat_id is not None:
                        year_part = (
                            f" (PYQ Year: {question.year})" if question.year is not None else ""
                        )
                        admin_msg = (
                            f"⚠️ Failed to send question Q{q_number}{year_part} "
                            f"(id={question.id}) to chat `{self.group_chat_id}`.\n"
                            f"Error: `{exc}`"
                        )
                        try:
                            await application.bot.send_message(
                                chat_id=self.admin_chat_id,
                                text=admin_msg,
                                parse_mode="Markdown",
                            )
                        except Exception as admin_exc:  # noqa: BLE001
                            logger.error(
                                "%s Additionally failed to notify admin about error: %s",
                                LOG_MARKER,
                                admin_exc,
                            )
                    continue

            await self._send_recap(application)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error during quiz run: %s", exc)
        finally:
            logger.info("%s Quiz session finished. Clearing active session.", LOG_MARKER)
            self._active_session = None
            application.stop_running()

    def _get_correct_users_for_question(
        self, question_index: int, correct_index: int
    ) -> List[str]:
        """
        Return display names (e.g., @username) of users who answered correctly.
        """
        session = self._active_session
        if session is None:
            return []

        question_answers = session.answers.get(question_index, {})
        user_ids: Set[int] = {
            user_id
            for user_id, chosen_index in question_answers.items()
            if chosen_index == correct_index
        }

        result: List[str] = []
        for user_id in user_ids:
            display_name = session.usernames.get(user_id)
            if not display_name:
                # Fallback: show raw ID if we somehow missed the user
                display_name = f"user_id:{user_id}"
            result.append(display_name)

        # Sort for deterministic output
        return sorted(result)

    async def handle_poll_answer(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        PollAnswerHandler entrypoint. Tracks answers per active quiz session.
        """
        poll_answer = update.poll_answer
        if not poll_answer:
            return

        session = self._active_session
        if session is None:
            logger.debug("Received poll answer but no active session. Ignoring.")
            return

        poll_id = poll_answer.poll_id
        question_index = session.poll_to_question_index.get(poll_id)
        if question_index is None:
            logger.debug("Poll answer for unknown poll_id %s. Ignoring.", poll_id)
            return

        user = poll_answer.user
        user_id = user.id

        # For quiz polls, there should be exactly one selected option
        if not poll_answer.option_ids:
            logger.debug("User %s cleared selection for poll %s", user_id, poll_id)
            return

        chosen_index = poll_answer.option_ids[0]

        if question_index not in session.answers:
            session.answers[question_index] = {}

        session.answers[question_index][user_id] = chosen_index

        # Record timing for the user's final answer to this question.
        # We measure elapsed time from when the poll was sent.
        q_start = session.question_start_times.get(question_index)
        if q_start is not None:
            elapsed = (datetime.now() - q_start).total_seconds()
            if question_index not in session.answer_times:
                session.answer_times[question_index] = {}
            session.answer_times[question_index][user_id] = elapsed

        # Cache a human-readable display name for this user
        username = getattr(user, "username", None)
        if username:
            display_name = f"@{username}"
        else:
            name_parts = [user.first_name or "", user.last_name or ""]
            display_name = " ".join(p for p in name_parts if p).strip() or "Unknown"

        session.usernames[user_id] = display_name
        logger.info(
            "%s Recorded answer: question_index=%s user_id=%s (%s) option_index=%s",
            LOG_MARKER,
            question_index,
            user_id,
            display_name,
            chosen_index,
        )

    async def _send_recap(self, application: Application) -> None:
        """
        After quiz completion, compute scores and send leaderboard and closing message.
        Per-question explanations are already sent inline during the quiz.
        """
        session = self._active_session
        if session is None:
            return

        # Aggregate per-user scores and total time across all questions.
        user_scores: Dict[int, int] = {}
        user_total_time: Dict[int, float] = {}

        for idx, question in enumerate(session.questions):
            # Only score questions that were successfully sent
            if idx in session.asked_question_indices:
                answers_for_question = session.answers.get(idx, {})
                answer_times_for_question = session.answer_times.get(idx, {})
                for user_id, chosen_index in answers_for_question.items():
                    if chosen_index == question.correct_index:
                        user_scores[user_id] = user_scores.get(user_id, 0) + 1
                        elapsed = answer_times_for_question.get(user_id)
                        if elapsed is None:
                            elapsed = float(
                                QUIZ_POLL_DURATION_SECONDS
                                + QUIZ_POLL_ANSWER_BUFFER_SECONDS
                            )
                        user_total_time[user_id] = user_total_time.get(user_id, 0.0) + elapsed

        # Always send leaderboard header.
        await application.bot.send_message(
            chat_id=self.group_chat_id,
            text="*Leaderboard (Top 10)*",
            parse_mode="Markdown",
        )

        if not user_scores:
            # No winners at all.
            await application.bot.send_message(
                chat_id=self.group_chat_id,
                text="No winners today.",
            )

            if self.admin_chat_id is not None:
                try:
                    await application.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text="*Leaderboard (Top 10)*",
                        parse_mode="Markdown",
                    )
                    await application.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text="No winners today.",
                    )
                except Exception as admin_exc:  # noqa: BLE001
                    logger.error(
                        "%s Failed to send leaderboard to admin: %s",
                        LOG_MARKER,
                        admin_exc,
                    )
        else:
            # Sort by descending score, then by total time ascending, then by display name.
            sorted_users = sorted(
                user_scores.items(),
                key=lambda kv: (
                    -kv[1],
                    user_total_time.get(kv[0], 0.0),
                    session.usernames.get(kv[0], f"user_id:{kv[0]}"),
                ),
            )
            top_users = sorted_users[:10]

            # Assign ranks; ties only if both score and time match.
            rank_map: Dict[int, int] = {}
            prev_score: Optional[int] = None
            prev_time: Optional[float] = None
            current_rank = 0
            for idx, (user_id, score) in enumerate(top_users):
                total_time = user_total_time.get(user_id, 0.0)
                if (
                    prev_score is None
                    or score != prev_score
                    or prev_time is None
                    or abs(total_time - prev_time) > 1e-6
                ):
                    current_rank = idx + 1
                    prev_score = score
                    prev_time = total_time
                rank_map[user_id] = current_rank

            lines: List[str] = []
            for rank in range(1, 11):
                users_at_rank = [
                    (user_id, user_scores[user_id])
                    for user_id, r in rank_map.items()
                    if r == rank
                ]
                if not users_at_rank:
                    lines.append(f"Rank {rank}: None")
                    continue
                for user_id, score in users_at_rank:
                    display_name = session.usernames.get(
                        user_id, f"user_id:{user_id}"
                    )
                    total_time = user_total_time.get(user_id, 0.0)
                    seconds = int(round(total_time))
                    lines.append(
                        f"Rank {rank}: {display_name} - {score} correct in {seconds} seconds"
                    )

            leaderboard_text = "\n".join(lines)

            await application.bot.send_message(
                chat_id=self.group_chat_id,
                text=leaderboard_text,
            )

            if self.admin_chat_id is not None:
                try:
                    await application.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text="*Leaderboard (Top 10)*",
                        parse_mode="Markdown",
                    )
                    await application.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text=leaderboard_text,
                    )
                except Exception as admin_exc:  # noqa: BLE001
                    logger.error(
                        "%s Failed to send leaderboard to admin: %s",
                        LOG_MARKER,
                        admin_exc,
                    )

        await application.bot.send_message(
            chat_id=self.group_chat_id,
            text="🏁 Daily Quiz Completed.\nThanks for joining!",
        )

