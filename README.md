# UPSC Wars Telegram Quiz Bot

Async daily quiz bot for Telegram using Python 3.11 and `python-telegram-bot` v20+.  
The bot loads and preprocesses questions on **every startup** from `data_raw/` and runs a 10-question quiz **once per day at 9:00 PM IST** in a configured group.

## Project Structure

- `main.py` – Entry point, wiring the bot, handlers, and scheduler.
- `scheduler.py` – Daily scheduling logic for the 9 PM IST quiz and missed-quiz catch-up.
- `quiz_engine.py` – Quiz logic: question sampling, poll lifecycle, answer tracking, recap.
- `question_loader.py` – Dynamic question loading and preprocessing from `data_raw/`.
- `requirements.txt` – Python dependencies.
- `data_raw/` – Directory containing raw JSON question files (you provide these).

## Requirements

- Python **3.11** (recommended virtualenv)
- A Telegram bot token from [BotFather](https://t.me/BotFather)
- A target Telegram **group chat ID**

## Installing Dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Preparing Questions

Place your raw JSON files inside `data_raw/`. Each file must contain a **list** of question objects.  
On every startup, `question_loader.load_and_preprocess_questions()` will:

- Load all `*.json` files from `data_raw/`.
- Filter out questions where `question["audit"]["has_table"] == True`.
- Extract:
  - `meta.uuid`
  - `presentation.display_text`
  - `solution.correct_option`
  - `solution.final_explanation`
- Parse options from `display_text` with regex:
  - `pattern = r"(\\([a-dA-D]\\)\\s*(.*?)\\s*(?=\\([a-dA-D]\\)|$))"`
- Require exactly **4 options**; otherwise the question is skipped.
- Compute `question_text` as everything before the first `(a)` / `(A)`.
- Map `correct_option` (`a`/`b`/`c`/`d` or similar) to `correct_index` 0–3.
- Raise an error if fewer than **10 valid questions** remain.

Runtime question format:

```python
{
    "id": "<uuid>",
    "question_text": "<question>",
    "options": ["opt1", "opt2", "opt3", "opt4"],
    "correct_index": 0,  # 0..3
    "explanation": "<final explanation>",
}
```

## Environment Variables

Set the following environment variables (no hardcoding in code):

- `TELEGRAM_TOKEN` – Bot API token from BotFather.
- `GROUP_CHAT_ID` – Integer chat ID of the target group (e.g. `-1001234567890`).

Example (Unix shells):

```bash
export TELEGRAM_TOKEN="123456:ABC-DEF..."
export GROUP_CHAT_ID="-1001234567890"
```

## Running the Bot

From the project root:

```bash
python main.py
```

Logging will show when questions are loaded and when the scheduler is configured.

## Quiz Behaviour

- **Scheduling**
  - Timezone: `Asia/Kolkata` via `zoneinfo`.
  - Daily quiz scheduled at **9:00 PM IST** using the JobQueue.
  - On startup, if the server time is **after 9 PM IST** and no quiz has run today, the quiz starts **immediately**.
  - Exactly **one quiz per day**, guarded by an async lock and `last_quiz_date`.

- **Quiz Flow**
  - 10 **unique** questions are chosen with `random.sample()`.
  - For each question:
    - Sends a `type="quiz"` poll:
      - `is_anonymous = False`
      - `correct_option_id` set from `correct_index`
      - `open_period = 60` seconds
    - Answers are tracked via `PollAnswerHandler` in `quiz_engine.QuizManager.handle_poll_answer`.
    - After ~60 seconds (with buffer), it:
      - Announces `Qn - Correct Answer: <A/B/C/D>`.
      - Lists **usernames / display names** of users who answered correctly.
      - **Does not** send the explanation here.
    - Waits 10 seconds, then moves to the next question.

- **End of Session**
  - Sends a recap covering all questions:
    - `Qn - Correct Answer: <A/B/C/D>`
    - `Correct Users` list for each question.
    - `Explanation` text from the source data.
  - Recap is automatically split into chunks under Telegram’s 4096 character limit.
  - Finally sends:
    - `🏁 Daily Quiz Completed. See you tomorrow at 9 PM!`

## Notes for Production Deployment

- Run the bot under a process supervisor (systemd, Supervisor, Docker, etc.) so it restarts on crashes.
- Ensure the server timezone is irrelevant – the bot uses `Asia/Kolkata` explicitly for scheduling.
- Configure logs to rotate or stream to your observability stack as needed.
- Keep the `data_raw/` directory up to date; any changes in raw questions will be picked up **on the next startup**.

