"""
Parse the 2024–2028 Extra Class (FCC Element 4) question pool PDF to JSON.

Rules (per user requirements):
- A question ALWAYS starts on a line that begins with "E<digit><letter><two digits>", e.g. "E1A01".
- Each question block ends with a line containing exactly "~~".
- Ignore any other text between/around questions (e.g., title pages, notes).
- Header format: E1A01 (D) [optional refs] <question text may start here or next lines>
- Choices: lines starting with "A.", "B.", "C.", "D." (can wrap across lines).

Schema:
- id: unique sequential primary key (1..N)
- question: full question text
- class: always "E"
- subelement: digit after leading E (E1A01 -> "1")
- group_index: letter after subelement (E1A01 -> "A")
- group_number: two digits after group_index (E1A01 -> "01")
- answer: correct answer letter from parentheses in header (A-D)
- answer_a .. answer_d: choice texts; multi-line safe
"""

import argparse
import json
import re
import sys
from pathlib import Path

from PyPDF2 import PdfReader

# Header must be at START of the line (ignore any other text)
# Examples: "E1A01 (D) [97.305, 97.307(b)] Why ... ?"
HEADER_RE = re.compile(
    r'^\s*'  # <-- allow leading spaces
    r'(?P<code>E(?P<subelement>\d)(?P<group_index>[A-Z])(?P<group_number>\d{2}))'
    r'\s*\((?P<answer>[A-D])\)\s*'
    r'(?:\[[^\]]*\]\s*)?'
    r'(?P<qstart>.*)$'
)

CHOICE_RE = re.compile(r'^\s*([A-D])\.\s*(.*\S)?\s*$')
SEP_RE    = re.compile(r'^\s*~~\s*$')  # guaranteed separator

def extract_pdf_lines(pdf_path: Path):
    """Return list of text lines from all pages, preserving order."""
    reader = PdfReader(pdf_path.as_posix())
    out = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        for ln in txt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            ln = ln.replace("\u00a0", " ").replace("\xad", "")  # nbsp, soft hyphen
            out.append(ln.rstrip())
    return out

def dehyphen_join(prev: str, nxt: str) -> str:
    """Join if previous line ends with a hyphen and next begins lowercase; else space-join."""
    if prev.endswith("-") and nxt[:1].islower():
        return prev[:-1] + nxt.lstrip()
    return (prev + " " + nxt.strip()).strip()

def parse_pool(lines, force_class="E"):
    items = []
    qid = 1
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Only treat as a new question if the line STARTS with the header pattern
        m = HEADER_RE.match(line)
        if not m:
            i += 1
            continue

        gd = m.groupdict()
        # Initialize record
        rec = {
            "id": qid,
            "question": "",
            "class": force_class,
            "subelement": gd["subelement"],
            "group_index": gd["group_index"],
            "group_number": gd["group_number"],
            "answer": gd["answer"],
            "answer_a": "",
            "answer_b": "",
            "answer_c": "",
            "answer_d": "",
        }
        qid += 1

        # Build question text: start with qstart (may be empty), then add lines
        # until we encounter a choice, a new header (shouldn't happen within block),
        # or the guaranteed separator "~~".
        q_parts = []
        if gd["qstart"].strip():
            q_parts.append(gd["qstart"].strip())

        i += 1
        while i < n:
            ln = lines[i]
            if SEP_RE.match(ln):  # end of this question block
                break
            if HEADER_RE.match(ln):  # safety: unexpected header before separator
                break
            if CHOICE_RE.match(ln):  # stop question text at first choice
                break
            if ln.strip():
                if q_parts:
                    q_parts[-1] = dehyphen_join(q_parts[-1], ln.strip())
                else:
                    q_parts.append(ln.strip())
            i += 1

        rec["question"] = " ".join(q_parts).strip()

        # Collect choices A..D (multi-line), until the separator or unexpected header
        choice_bufs = {"A": [], "B": [], "C": [], "D": []}
        current = None

        while i < n:
            ln = lines[i]
            if SEP_RE.match(ln) or HEADER_RE.match(ln):
                break

            cm = CHOICE_RE.match(ln)
            if cm:
                current = cm.group(1)  # A/B/C/D
                first_text = (cm.group(2) or "").strip()
                if first_text:
                    choice_bufs[current].append(first_text)
                i += 1
                continue

            if current:
                cont = ln.strip()
                if cont:
                    if choice_bufs[current]:
                        choice_bufs[current][-1] = dehyphen_join(choice_bufs[current][-1], cont)
                    else:
                        choice_bufs[current].append(cont)
                i += 1
                continue

            # Uninteresting line between header/question and first choice
            i += 1

        # Clean assignment
        def clean(parts):
            s = " ".join(p for p in parts if p).strip()
            return re.sub(r"\s{2,}", " ", s)

        rec["answer_a"] = clean(choice_bufs["A"])
        rec["answer_b"] = clean(choice_bufs["B"])
        rec["answer_c"] = clean(choice_bufs["C"])
        rec["answer_d"] = clean(choice_bufs["D"])

        items.append(rec)

        # Consume the required separator if present
        if i < n and SEP_RE.match(lines[i]):
            i += 1

        # Continue scanning for the next header from current i (no extra increment)

    return items

def main():
    ap = argparse.ArgumentParser(description="Parse Extra Class question pool PDF to JSON (PyPDF2).")
    ap.add_argument("pdf", help="Path to the pool PDF (e.g., extra_exam_pool.pdf)")
    ap.add_argument("-o", "--out", help="Output JSON path (default: stdout)")
    ap.add_argument("--class-default", default="E", help="Value for 'class' field (default: E)")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    lines = extract_pdf_lines(pdf_path)
    records = parse_pool(lines, force_class=args.class_default)

    data = json.dumps(records, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(data, encoding="utf-8")
        print(f"Wrote {len(records)} records to {args.out}")
    else:
        print(data)

if __name__ == "__main__":
    main()
