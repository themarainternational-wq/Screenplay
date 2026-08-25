"""
chunker.py
----------
Splits a list of (block_type, text) blocks into ordered chunks that stay
under a safe character budget for the translation/TTS models, without
ever splitting a block across two chunks (so a character's dialogue line
never gets cut in half) and without ever reordering content.

If a single block itself is longer than max_chars (a big action
paragraph), it is further split on sentence boundaries only, never
mid-sentence.
"""

import re

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?।])\s+|(?<=[\u0BCD\u0B83])\s+')


def _split_long_block(block_type, text, max_chars):
    if len(text) <= max_chars:
        return [(block_type, text)]

    sentences = SENTENCE_SPLIT_RE.split(text)
    pieces = []
    current = ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > max_chars:
            pieces.append((block_type, current.strip()))
            current = s
        else:
            current = (current + " " + s).strip()
    if current:
        pieces.append((block_type, current.strip()))

    # Fallback: a single sentence longer than max_chars with no
    # punctuation at all - hard-split so nothing ever blocks the pipeline.
    final = []
    for btype, txt in pieces:
        if len(txt) <= max_chars:
            final.append((btype, txt))
        else:
            for i in range(0, len(txt), max_chars):
                final.append((btype, txt[i:i + max_chars]))
    return final


def chunk_blocks(blocks, max_chars=1200):
    """Returns a list of chunks. Each chunk is a list of (block_type, text)."""
    expanded = []
    for btype, text in blocks:
        expanded.extend(_split_long_block(btype, text, max_chars))

    chunks = []
    current = []
    current_len = 0
    for btype, text in expanded:
        blen = len(text)
        if current and current_len + blen > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append((btype, text))
        current_len += blen
    if current:
        chunks.append(current)

    return chunks
