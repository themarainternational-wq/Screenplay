"""
extract.py
----------
Reads PDF / DOCX / TXT files and turns them into an ordered list of
"blocks": (block_type, text) tuples.

block_type is one of:
    SCENE_HEADING, CHARACTER, DIALOGUE, PARENTHETICAL,
    TRANSITION, ACTION

This is a best-effort heuristic classifier. Screenplay formatting varies
a lot (Tamil scripts especially don't follow the Hollywood ALL-CAPS
convention strictly), so it will not be perfect on every document. It is
tuned to fail safely: anything it can't confidently classify becomes
ACTION, which is always translated/read normally and never breaks the
pipeline.
"""

import re
import fitz  # PyMuPDF
import docx  # python-docx

TAMIL_RANGE = (0x0B80, 0x0BFF)

SCENE_HEADING_RE = re.compile(
    r'^\s*(INT|EXT|INT/EXT|I/E)[\.\s]', re.IGNORECASE
)
SCENE_HEADING_TA_RE = re.compile(r'^\s*(காட்சி|இடம்)\s*[:\-]?')
TRANSITION_RE = re.compile(
    r'(CUT TO\s*:?|FADE IN\s*:?|FADE OUT\s*:?|DISSOLVE TO\s*:?|SMASH CUT\s*:?)\s*$',
    re.IGNORECASE
)
PARENTHETICAL_RE = re.compile(r'^\s*\(.+\)\s*$')


def _is_tamil_char(ch):
    return TAMIL_RANGE[0] <= ord(ch) <= TAMIL_RANGE[1]


def _looks_like_character_cue(line, next_line):
    """
    Heuristic: a character cue is short, has no trailing sentence
    punctuation, and is usually followed by dialogue (a longer line,
    or a parenthetical).
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 40:
        return False
    if stripped.endswith(('.', ',', ';', '?', '!')):
        return False

    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False

    tamil_ratio = sum(1 for c in letters if _is_tamil_char(c)) / len(letters)
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)

    # English-style: mostly uppercase Latin letters (RAVI, MEENA (V.O.))
    # Tamil: no concept of case, so we lean on "short line, next line is
    # longer / is dialogue-shaped" instead.
    if upper_ratio > 0.8:
        return True
    if tamil_ratio > 0.6:
        nxt = (next_line or "").strip()
        if nxt and len(nxt) > len(stripped):
            return True
    return False


def classify_lines(lines):
    """Turn a flat list of raw text lines into (block_type, text) blocks,
    merging consecutive lines of the same type (e.g. multi-line action
    paragraphs, multi-line dialogue)."""
    blocks = []
    prev_type = None

    for i, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            prev_type = "BLANK"
            continue

        if SCENE_HEADING_RE.match(stripped) or SCENE_HEADING_TA_RE.match(stripped):
            btype = "SCENE_HEADING"
        elif TRANSITION_RE.search(stripped):
            btype = "TRANSITION"
        elif PARENTHETICAL_RE.match(stripped):
            btype = "PARENTHETICAL"
        elif _looks_like_character_cue(stripped, lines[i + 1] if i + 1 < len(lines) else ""):
            btype = "CHARACTER"
        elif prev_type in ("CHARACTER", "PARENTHETICAL", "DIALOGUE"):
            btype = "DIALOGUE"
        else:
            btype = "ACTION"

        if blocks and blocks[-1][0] == btype and btype in ("ACTION", "DIALOGUE"):
            # merge wrapped lines of the same paragraph
            blocks[-1] = (btype, blocks[-1][1] + " " + stripped)
        else:
            blocks.append((btype, stripped))

        prev_type = btype

    return blocks


def extract_pdf(path):
    doc = fitz.open(path)
    lines = []
    for page in doc:
        text = page.get_text("text")
        for line in text.split("\n"):
            lines.append(line)
        lines.append("")  # page break -> paragraph break
    doc.close()
    return classify_lines(lines)


def extract_docx(path):
    d = docx.Document(path)
    lines = [p.text for p in d.paragraphs]
    return classify_lines(lines)


def extract_txt(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    return classify_lines(lines)


def extract(path):
    """Dispatch by file extension. Returns list of (block_type, text)."""
    lower = path.lower()
    if lower.endswith(".pdf"):
        return extract_pdf(path)
    elif lower.endswith(".docx"):
        return extract_docx(path)
    elif lower.endswith(".txt"):
        return extract_txt(path)
    else:
        raise ValueError(f"Unsupported file type: {path}")
