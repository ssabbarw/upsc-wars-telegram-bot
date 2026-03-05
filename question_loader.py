import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)

# Extract options labeled (a)...(d) from the question text.
OPTION_PATTERN = re.compile(r"\([a-dA-D]\)\s*(.*?)\s*(?=\([a-dA-D]\)|$)")


@dataclass
class ProcessedQuestion:
    id: str
    question_text: str
    correct_index: int
    explanation: str
    year: int | None = None
    # Optional: separated options text (without labels), if we could parse them.
    options: Optional[List[str]] = None
    # Optional: structured statement analysis from solution.statement_analysis.
    statement_analysis: Optional[List[Dict[str, str]]] = None
    # Optional: elimination logic points from solution.elimination_logic.
    elimination_logic: Optional[List[str]] = None



def _normalize_markdown(text: str) -> str:
    """
    Convert **bold** markers (used in source JSON) to *bold* so they render
    correctly under Telegram's legacy Markdown mode.
    """
    if not text:
        return text
    # Replace all non-greedy **...** segments with *...*
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)




def _extract_question_text(display_text: str) -> str | None:
    """
    Return the full display_text including options.

    Earlier we stripped everything after the first '(a)' marker to separate the
    stem from the options. Since we now show only A/B/C/D in the poll and want
    the full statement (including options) in the message, we no longer cut at
    '(a)' and just use the entire formatted_presentation/display_text.
    """
    text = (display_text or "").strip()
    return text or None


def _parse_correct_index(correct_option: str) -> int | None:
    """Map correct option like 'a', 'A', 'a)' to index 0-3."""
    if not correct_option:
        return None
    first_char = str(correct_option).strip()[0].lower()
    mapping = {"a": 0, "b": 1, "c": 2, "d": 3}
    return mapping.get(first_char)


def load_and_preprocess_questions(data_dir: str | Path = "data_raw") -> List[dict]:
    """
    Load all JSON files from data_raw/, filter and normalize questions.

    Returns a list of dicts with the runtime format:
    {
      "id": uuid,
      "question_text": "...",
      "options": ["opt1", "opt2", "opt3", "opt4"],
      "correct_index": 0,
      "explanation": "..."
    }
    """
    base_path = Path(data_dir)
    if not base_path.exists() or not base_path.is_dir():
        msg = f"Question directory not found: {base_path.resolve()}"
        logger.error(msg)
        raise FileNotFoundError(msg)

    processed: List[ProcessedQuestion] = []
    skipped_count = 0

    for json_file in sorted(base_path.glob("*.json")):
        logger.info("Loading questions from %s", json_file)
        try:
            with json_file.open("r", encoding="utf-8") as f:
                raw_questions = json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load %s: %s", json_file, exc)
            continue

        if not isinstance(raw_questions, list):
            logger.warning("File %s does not contain a list. Skipping.", json_file)
            continue

        for q in raw_questions:
            try:
                meta = q.get("meta") or {}
                uuid = meta.get("uuid")
                year = meta.get("year")

                # Prefer formatted_presentation.display_text if available,
                # fall back to presentation.display_text.
                formatted_presentation = q.get("formatted_presentation") or {}
                presentation = q.get("presentation") or {}
                display_text = (
                    formatted_presentation.get("display_text")
                    or presentation.get("display_text")
                    or ""
                )
                solution = q.get("solution") or {}
                correct_option = solution.get("correct_option")
                explanation = solution.get("final_explanation") or ""
                raw_statement_analysis = solution.get("statement_analysis") or []
                raw_elimination_logic = solution.get("elimination_logic") or []

                if not uuid or not display_text or correct_option is None:
                    skipped_count += 1
                    continue

                question_text = _extract_question_text(display_text)
                if not question_text:
                    skipped_count += 1
                    continue

                # Try to extract up to four options; if parsing fails, we keep
                # options=None and fall back to generic A/B/C/D poll options.
                raw_options = OPTION_PATTERN.findall(display_text)
                options: Optional[List[str]] = None
                if len(raw_options) == 4:
                    options = [
                        _normalize_markdown(opt.strip()) for opt in raw_options
                    ]

                correct_index = _parse_correct_index(correct_option)
                if correct_index is None or not (0 <= correct_index < 4):
                    skipped_count += 1
                    continue

                # Normalize statement_analysis entries (if present) for Markdown.
                statement_analysis: Optional[List[Dict[str, str]]] = None
                if isinstance(raw_statement_analysis, list) and raw_statement_analysis:
                    statement_analysis = []
                    for entry in raw_statement_analysis:
                        if not isinstance(entry, dict):
                            continue
                        stmt = _normalize_markdown(str(entry.get("statement", "")).strip())
                        verdict = _normalize_markdown(str(entry.get("verdict", "")).strip())
                        reason = _normalize_markdown(str(entry.get("reason", "")).strip())
                        if not stmt and not verdict and not reason:
                            continue
                        statement_analysis.append(
                            {
                                "statement": stmt,
                                "verdict": verdict,
                                "reason": reason,
                            }
                        )
                    if not statement_analysis:
                        statement_analysis = None

                # Normalize elimination_logic entries (if present) for Markdown.
                elimination_logic: Optional[List[str]] = None
                if isinstance(raw_elimination_logic, list) and raw_elimination_logic:
                    elimination_logic = []
                    for item in raw_elimination_logic:
                        text = _normalize_markdown(str(item).strip())
                        if text:
                            elimination_logic.append(text)
                    if not elimination_logic:
                        elimination_logic = None

                processed.append(
                    ProcessedQuestion(
                        id=str(uuid),
                        question_text=_normalize_markdown(question_text.strip()),
                        correct_index=correct_index,
                        explanation=_normalize_markdown(str(explanation).strip()),
                        year=int(year) if isinstance(year, int) else None,
                        options=options,
                        statement_analysis=statement_analysis,
                        elimination_logic=elimination_logic,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                skipped_count += 1
                logger.exception("Error processing question from %s: %s", json_file, exc)

    logger.info("Processed %d questions. Skipped %d.", len(processed), skipped_count)

    if len(processed) < 10:
        msg = f"Not enough valid questions after preprocessing: {len(processed)} found, 10 required."
        logger.error(msg)
        raise RuntimeError(msg)

    # Convert to plain dicts for runtime usage
    return [
        {
            "id": q.id,
            "question_text": q.question_text,
            "correct_index": q.correct_index,
            "explanation": q.explanation,
            "year": q.year,
            "options": q.options,
            "statement_analysis": q.statement_analysis,
            "elimination_logic": q.elimination_logic,
        }
        for q in processed
    ]

