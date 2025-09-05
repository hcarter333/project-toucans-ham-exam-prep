"""
exam_repl.py — Chat-friendly driver for the Extra Class practice exam
"""

from __future__ import annotations
import re
from pathlib import Path

# Stable location so state persists across chat turns
DATA_DIR = Path('/mnt/data')

try:
    from extra_exam_local import ExamSession   # prefer local-images version
except Exception:
    from extra_exam import ExamSession         # fallback if only original exists


def _has_unanswered(sess: ExamSession) -> bool:
    for qid in sess.current.selected_ids:
        if int(qid) not in sess.current.answered:
            return True
    return False


def _help_text() -> str:
    return (
        "Commands:\n"
        "  • New Test — start a new practice test; prints first question; emits base64 snapshot\n"
        "  • A Submit (or B/C/D Submit) — submit the answer for the last-shown question;\n"
        "      prints feedback; if the test ends, shows summary, charts, and a snapshot\n"
    )


def chat_repl(command: str) -> str:
    """Handle one chat command and return printable text."""
    sess = ExamSession(pool_path=DATA_DIR / 'extra_pool.json',
                       state_path=DATA_DIR / 'exam_state.json')
    t = (command or "").strip()

    if t.lower() in ("help", "?", "commands"):
        return _help_text()

    if t.lower() == "new test":
        sess.start_new_exam()
        first_q = sess.next_question_markdown()
        b64 = sess.dump_state_base64()
        return (
            "Started a new test.\n\n"
            "Session snapshot (base64, keep this in chat to reload later):\n"
            f"{b64}\n\n"
            "First question:\n\n" + first_q
        )

    m = re.match(r"^([A-Da-d])\s*submit$", t, re.IGNORECASE)
    if m:
        letter = m.group(1).upper()

        # If nothing is active yet, activate a question before grading
        if sess.current.last_shown_id is None and _has_unanswered(sess):
            _ = sess.next_question_markdown()

        result = sess.answer_current(letter)

        if not _has_unanswered(sess):
            summary = sess.finalize()
            sess.render_all_charts()
            b64 = sess.dump_state_base64()
            return (
                f"{result}\n\n"
                f"{summary}\n\n"
                "Session snapshot (base64, keep this in chat to reload later):\n"
                f"{b64}"
            )
        else:
            next_q = sess.next_question_markdown()
            return f"{result}\n\nNext question:\n\n{next_q}"

    return _help_text()


if __name__ == "__main__":
    import sys
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(chat_repl(cmd or "help"))
