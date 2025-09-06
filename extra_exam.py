"""
extra_exam.py — prefers local figure PNGs (img/<slug>.png) and
adds robust state handling for ChatGPT Projects.
"""

from __future__ import annotations
import base64, json, io, os, random, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    import urllib.request
except Exception:
    plt = None
    mpimg = None

# Stable paths
DEFAULT_DIR = Path(os.getenv("EXAM_DATA_DIR", "/mnt/data"))
POOL_PATH = Path(os.getenv("EXTRA_POOL_PATH", str(DEFAULT_DIR / "extra_pool.json")))
STATE_PATH = Path(os.getenv("EXAM_STATE_PATH", str(DEFAULT_DIR / "exam_state.json")))

FIG_BASE_URL = "https://raw.githubusercontent.com/hcarter333/project-toucans-ham-exam-prep/refs/heads/main/img/"
FIG_LOCAL_DIR = Path("img")

ANSWER_MAP = {"A": "answer_a", "B": "answer_b", "C": "answer_c", "D": "answer_d"}
FIGURE_RE = re.compile(r"Figure\s+([A-Za-z])\s*([0-9]+)\s*[-–—]\s*([0-9]+)", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _group_key(q: dict) -> str: return f"E{q.get('subelement')}{q.get('group_index')}"
def _sub_key(q: dict) -> str: return f"E{q.get('subelement')}"


def find_figure_slug(text: str) -> Optional[str]:
    if not text: return None
    m = FIGURE_RE.search(text)
    if not m: return None
    return f"{m.group(1).lower()}{m.group(2)}-{m.group(3)}"


@dataclass
class Stats:
    history: List[Dict] = field(default_factory=list)
    subelements: Dict[str, Dict[str, int]] = field(default_factory=dict)
    groups: Dict[str, Dict[str, Dict[str, int]]] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Stats":
        s = Stats()
        s.history = list(d.get("history", []))
        s.subelements = dict(d.get("subelements", {}))
        s.groups = dict(d.get("groups", {}))
        return s

    def to_dict(self) -> dict:
        return {"history": self.history, "subelements": self.subelements, "groups": self.groups}


@dataclass
class CurrentExam:
    selected_ids: List[int] = field(default_factory=list)
    presented_ids: List[int] = field(default_factory=list)
    answered: Dict[int, Dict] = field(default_factory=dict)
    correct_ids: List[int] = field(default_factory=list)
    wrong_ids: List[int] = field(default_factory=list)
    done: bool = False
    last_shown_id: Optional[int] = None

    @staticmethod
    def from_dict(d: dict) -> "CurrentExam":
        # NOTE: JSON forces object keys to strings. Convert answered keys back to int.
        answered_raw = d.get("answered", {})
        try:
            answered_norm = {int(k): v for k, v in answered_raw.items()}
        except Exception:
            # If anything odd slips in, fall back to an empty dict rather than crashing
            answered_norm = {}

        return CurrentExam(
            selected_ids=[int(x) for x in d.get("selected_ids", [])],
            presented_ids=[int(x) for x in d.get("presented_ids", [])],
            answered=answered_norm,
            correct_ids=[int(x) for x in d.get("correct_ids", [])],
            wrong_ids=[int(x) for x in d.get("wrong_ids", [])],
            done=bool(d.get("done", False)),
            last_shown_id=(int(d["last_shown_id"]) if d.get("last_shown_id") is not None else None),
        )

    def to_dict(self) -> dict:
        return {
            "selected_ids": self.selected_ids,
            "presented_ids": self.presented_ids,
            "answered": self.answered,
            "correct_ids": self.correct_ids,
            "wrong_ids": self.wrong_ids,
            "done": self.done,
            "last_shown_id": self.last_shown_id,
        }


class ExamSession:
    def __init__(self, pool_path: Path = POOL_PATH, state_path: Path = STATE_PATH, seed: Optional[int] = None):
        if not pool_path.exists():
            raise FileNotFoundError(f"Could not find {pool_path}. Upload extra_pool.json to your Project first.")
        self.pool_path, self.state_path = Path(pool_path), Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(seed)
        self.pool: List[dict] = self._load_pool()
        self.stats: Stats
        self.current: CurrentExam
        self._load_or_init_state()

    def _load_pool(self) -> List[dict]:
        with open(self.pool_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for q in data:
            if isinstance(q.get("id"), str) and q["id"].isdigit():
                q["id"] = int(q["id"])
        return data

    def _load_or_init_state(self) -> None:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                self.stats = Stats.from_dict(d.get("stats", {}))
                self.current = CurrentExam.from_dict(d.get("current", {}))
                return
            except Exception:
                pass
        self.stats, self.current = Stats(), CurrentExam()
        self._save_state()

    def _save_state(self) -> None:
        out = {"stats": self.stats.to_dict(), "current": self.current.to_dict()}
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    # --- NEW: state import/export helpers expected by exam_repl.py ---
    def dump_state_base64(self) -> str:
        """Return the entire state file (JSON) encoded as base64 text."""
        if not self.state_path.exists():
            self._save_state()
        with open(self.state_path, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode("ascii")

    def load_state_base64(self, b64: str) -> None:
        """Replace state from a base64 JSON dump and reload in-memory objects."""
        data = base64.b64decode((b64 or "").encode("ascii"))
        # Basic validation: must be valid JSON with 'stats' and 'current'
        obj = json.loads(data.decode("utf-8"))
        if not isinstance(obj, dict) or "stats" not in obj or "current" not in obj:
            raise ValueError("Provided data does not look like a valid session state")
        with open(self.state_path, "wb") as f:
            f.write(data)
        self._load_or_init_state()

    def start_new_exam(self) -> None:
        buckets: Dict[str, List[dict]] = {}
        for q in self.pool:
            buckets.setdefault(_group_key(q), []).append(q)
        chosen: List[dict] = [self.rng.choice(arr) for arr in buckets.values()]
        chosen.sort(key=lambda q: (int(q.get("subelement", 999)), str(q.get("group_index")), str(q.get("group_number"))))
        self.current = CurrentExam(selected_ids=[int(q["id"]) for q in chosen], presented_ids=[])
        self._save_state()

    def _q_by_id(self, qid: int) -> dict:
        for q in self.pool:
            if int(q.get("id")) == int(qid):
                return q
        raise KeyError(f"Question id {qid} not found")

    def _figure_md(self, slug: str) -> str:
        local_path = FIG_LOCAL_DIR / f"{slug}.png"
        if local_path.exists():
            return f"\n![Figure {slug.upper()}](sandbox:{local_path})\n"
        return f"\n![Figure {slug.upper()}]({FIG_BASE_URL}{slug}.png)\n"

    def _question_markdown(self, q: dict) -> str:
        qid, qtext = q.get("id"), (q.get("question") or "").strip().replace("\n", " ")
        header, slug = f"**E{q.get('subelement')}{q.get('group_index')}{q.get('group_number','')}** (id {qid})\n", find_figure_slug(qtext)
        fig_md = self._figure_md(slug) if slug else ""
        return "\n".join(
            [
                header,
                qtext + fig_md,
                "\nA. " + (q.get("answer_a") or ""),
                "\nB. " + (q.get("answer_b") or ""),
                "\nC. " + (q.get("answer_c") or ""),
                "\nD. " + (q.get("answer_d") or ""),
            ]
        )

    def next_question_markdown(self) -> str:
        for qid in self.current.selected_ids:
            if int(qid) not in self.current.answered:
                self.current.last_shown_id = int(qid)
                if int(qid) not in self.current.presented_ids:
                    self.current.presented_ids.append(int(qid))
                self._save_state()
                return self._question_markdown(self._q_by_id(qid))
        return "_(All questions have been answered. Call `finalize()`.)_"

    def _correct_letter(self, q: dict) -> str:
        return str(q.get("answer", "")).strip().upper()[:1]

    def answer(self, qid: int, letter: str) -> str:
        letter = str(letter).strip().upper()[:1]
        if letter not in ANSWER_MAP:
            return "Please use one of: A, B, C, D."
        q, correct = self._q_by_id(qid), self._correct_letter(self._q_by_id(qid))
        is_correct = (letter == correct)
        self.current.answered[int(qid)] = {"selected": letter, "correct": is_correct}
        if is_correct:
            if int(qid) not in self.current.correct_ids:
                self.current.correct_ids.append(int(qid))
            msg = "✅ Correct."
        else:
            if int(qid) not in self.current.wrong_ids:
                self.current.wrong_ids.append(int(qid))
            correct_text = q.get(ANSWER_MAP.get(correct)) or correct
            msg = f"❌ Incorrect. Correct answer: {correct} — {correct_text}"
        self.current.last_shown_id = None  # clear active so next one shows
        self._save_state()
        return msg

    def answer_current(self, letter: str) -> str:
        if self.current.last_shown_id is None:
            return "No current question shown. Use `next_question_markdown()` first."
        return self.answer(self.current.last_shown_id, letter)

    def finalize(self) -> str:
        total, correct = len(self.current.selected_ids), len(self.current.correct_ids)
        if total == 0:
            return "_(No questions in the current exam.)_"
        pct, passed = round((correct / total) * 100), (len(self.current.correct_ids) / total >= 0.8)
        self.current.done = True
        # Update high-level stats
        self.stats.history.append({"ts": _now_iso(), "scorePct": pct})
        # Update per-subelement stats based on answered questions
        for qid, meta in self.current.answered.items():
            q = self._q_by_id(qid)
            subk = _sub_key(q)
            self.stats.subelements.setdefault(subk, {"right": 0, "wrong": 0})
            if meta.get("correct"):
                self.stats.subelements[subk]["right"] += 1
            else:
                self.stats.subelements[subk]["wrong"] += 1
        self._save_state()
        return ("You passed with " if passed else "You failed with ") + f"{pct}% ({correct}/{total} correct)."

    # --- NEW: basic chart renderer used by exam_repl.py ---
    def render_all_charts(self, out_dir: Path | None = None) -> List:
        """
        Render helpful charts to PNG files and also return live matplotlib figures
        for inline display. Returns a list containing Path and Figure objects.
        """
        out_dir = Path(out_dir or self.state_path.parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        created: List = []
    
        if plt is None:
            # Matplotlib not available; skip quietly
            return created
    
        import numpy as np
    
        # 1) Score history line chart
        if self.stats.history:
            xs = [i + 1 for i, _ in enumerate(self.stats.history)]
            ys = [int(h.get("scorePct", 0)) for h in self.stats.history]
    
            fig, ax = plt.subplots(figsize=(8, 6))
            ax.plot(xs, ys, marker="o")
            ax.set_xlabel("Attempt")
            ax.set_ylabel("Score (%)")
            ax.set_title("Practice Test Scores Over Time")
            ax.set_ylim(0, 100)
    
            p = out_dir / "score_history.png"
            fig.tight_layout()
            fig.savefig(p, bbox_inches="tight", dpi=150)
    
            created.append(p)
            created.append(fig)  # return live figure too
    
        # 2) Per-subelement bar chart
        if self.stats.subelements:
            labels = sorted(self.stats.subelements.keys())
            right = [self.stats.subelements[k].get("right", 0) for k in labels]
            wrong = [self.stats.subelements[k].get("wrong", 0) for k in labels]
    
            x = np.arange(len(labels))
    
            fig, ax = plt.subplots(figsize=(8, 6))
            ax.bar(x, wrong, label="Wrong")
            ax.bar(x, right, bottom=wrong, label="Right")
    
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=45, ha="right")
            ax.set_ylabel("Count")
            ax.set_title("Performance by Subelement")
            ax.legend()
    
            p2 = out_dir / "subelement_breakdown.png"
            fig.tight_layout()
            fig.savefig(p2, bbox_inches="tight", dpi=150)
    
            created.append(p2)
            created.append(fig)  # return live figure too
    
        return created
    
