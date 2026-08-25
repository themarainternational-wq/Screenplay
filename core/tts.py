"""
core/tts.py
------
Converts translated/original text to speech.

Two engines:

1. "local" (PRIVATE, CPU, quantized): MMS-TTS (facebook/mms-tts-tam,
   facebook/mms-tts-eng), open-source, no API key. Already a small
   model (~36M params/~150MB per language before quantization,
   ~50-75MB after dynamic int8 quantization) - the one piece of the
   previous architecture that didn't need to change model choice, just
   lose its GPU decoration. Text never leaves the server.

2. "online" (opt-in, NOT private, but uses almost no local RAM/CPU):
   Microsoft Edge's free read-aloud voices via `edge-tts`. Given how
   tight Streamlit Community Cloud's ~1GB shared budget is, this is
   genuinely the safer default if you're also translating in the same
   session - it does the heavy lifting on Microsoft's servers instead
   of competing with the translation model for this app's RAM.

ARCHITECTURE FOR STREAMLIT (no GPU, no quota - the constraint here is
RAM/CPU, not GPU minutes): large documents are still processed as a
resumable JOB, with progress written to disk piece-by-piece so nothing
depends on holding a whole document's audio in memory at once. Chunks
are bounded by a wall-clock time budget per processing "leg" instead of
a GPU-session duration.
"""

import os
import re
import shutil
import time

import numpy as np
import soundfile as sf
import streamlit as st
import torch
from pydub import AudioSegment

from core import jobstore

MMS_MODEL_IDS = {
    "ta": "facebook/mms-tts-tam",
    "en": "facebook/mms-tts-eng",
}
EDGE_VOICES = {
    "ta": "ta-IN-PallaviNeural",
    "en": "en-US-AriaNeural",
}

MAX_TTS_CHARS = 280
CHUNK_SECONDS_BUDGET = 8
WORDS_PER_PART = 1800
FULL_MERGE_MAX_SECONDS = 90 * 60

_uroman_instance = None
_uroman_checked = False


@st.cache_resource(show_spinner="Loading speech model (first time only)...")
def _load_local_model(lang):
    from transformers import VitsModel, AutoTokenizer
    model_id = MMS_MODEL_IDS[lang]
    model = VitsModel.from_pretrained(model_id)
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model.eval()
    # Dynamic int8 quantization - same rationale as translate.py.
    model = torch.quantization.quantize_dynamic(
        model, {torch.nn.Linear}, dtype=torch.qint8
    )
    return model, tokenizer


def _maybe_romanize(text, tokenizer):
    """Some MMS-TTS checkpoints require uroman pre-romanization (their
    tokenizer exposes is_uroman=True). Checked at runtime rather than
    assumed either way for Tamil."""
    global _uroman_instance, _uroman_checked
    if not getattr(tokenizer, "is_uroman", False):
        return text
    if not _uroman_checked:
        _uroman_checked = True
        try:
            import uroman as ur
            _uroman_instance = ur.Uroman()
        except Exception:
            _uroman_instance = None
    if _uroman_instance is not None:
        return _uroman_instance.romanize_string(text)
    print(
        "WARNING: this TTS voice expects romanized input (is_uroman=True) "
        "but the optional 'uroman' package is not installed - speech "
        "quality may be degraded for this voice."
    )
    return text


def _split_for_tts(text, max_chars=MAX_TTS_CHARS):
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    pieces, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) > max_chars:
            pieces.append(current.strip())
            current = s
        else:
            current = (current + " " + s).strip()
    if current:
        pieces.append(current.strip())
    final = []
    for p in pieces:
        if len(p) <= max_chars:
            final.append(p)
        else:
            for i in range(0, len(p), max_chars):
                final.append(p[i:i + max_chars])
    return [p for p in final if p.strip()]


def _synthesize_one(text, lang):
    model, tokenizer = _load_local_model(lang)
    text = _maybe_romanize(text, tokenizer)
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        output = model(**inputs).waveform
    wav = output.squeeze().detach().cpu().numpy()
    return wav, model.config.sampling_rate


def _ffmpeg_available():
    """Don't assume packages.txt worked - actually check the binary is
    on PATH before trying to use it, so a missing ffmpeg produces a
    clear message instead of a cryptic pydub stack trace."""
    return shutil.which("ffmpeg") is not None


# --------------------------------------------------------------- LOCAL JOB

def _prepare_tts_job_state(blocks, lang, out_dir):
    pieces = []
    for btype, text in blocks:
        if text.strip():
            pieces.extend(_split_for_tts(text))
    return {
        "kind": "tts",
        "lang": lang,
        "out_dir": out_dir,
        "pieces": pieces,
        "done": [False] * len(pieces),
        "piece_files": {},
        "last_error": None,
        "result": None,
    }


def start_or_get_tts_job(job_id, blocks, lang, out_dir):
    state = jobstore.load(job_id)
    if state is None:
        state = _prepare_tts_job_state(blocks, lang, out_dir)
        jobstore.save(job_id, state)
    return state


def run_tts_job(job_id, time_budget=CHUNK_SECONDS_BUDGET, progress_cb=None):
    if not _ffmpeg_available():
        state = jobstore.load(job_id) or {}
        state["last_error"] = (
            "FFmpeg isn't available on this server, so audio can't be "
            "converted to MP3. Check that packages.txt (containing "
            "'ffmpeg') is at the root of your GitHub repository."
        )
        return state, False, state["last_error"]

    state = jobstore.load(job_id)
    if state is None:
        raise ValueError(f"No such job: {job_id}")

    pieces = state["pieces"]
    out_dir = state["out_dir"]
    piece_dir = os.path.join(out_dir, "_pieces")
    os.makedirs(piece_dir, exist_ok=True)

    pending = [i for i, d in enumerate(state["done"]) if not d]

    stopped_reason = None
    start_time = time.time()
    processed_any = False
    for i in pending:
        if processed_any and (time.time() - start_time) > time_budget:
            break
        try:
            wav, sr = _synthesize_one(pieces[i], state["lang"])
        except Exception as e:
            stopped_reason = _describe_error(e)
            state["last_error"] = stopped_reason
            jobstore.save(job_id, state)
            break

        path = os.path.join(piece_dir, f"piece_{i:06d}.wav")
        sf.write(path, np.array(wav, dtype=np.float32), sr)
        state["piece_files"][str(i)] = path
        state["done"][i] = True
        processed_any = True

        jobstore.save(job_id, state)
        if progress_cb:
            progress_cb(sum(state["done"]), len(pieces))

    complete = all(state["done"]) if pieces else True
    if complete and state["result"] is None:
        state["result"] = _assemble_parts(state)
        jobstore.save(job_id, state)

    return state, complete, stopped_reason


def _assemble_parts(state):
    pieces = state["pieces"]
    out_dir = state["out_dir"]

    groups, current, wc = [], [], 0
    for i, text in enumerate(pieces):
        words = len(text.split())
        if current and wc + words > WORDS_PER_PART:
            groups.append(current)
            current, wc = [], 0
        current.append(i)
        wc += words
    if current:
        groups.append(current)

    part_paths, failed_parts, total_ms = [], [], 0
    for gi, idx_group in enumerate(groups):
        part_path = os.path.join(out_dir, f"Part {gi + 1:02d}.mp3")
        try:
            merged = AudioSegment.empty()
            pause = AudioSegment.silent(duration=350)
            for i in idx_group:
                path = state["piece_files"].get(str(i))
                if not path or not os.path.exists(path):
                    raise FileNotFoundError(f"missing piece {i}")
                merged += AudioSegment.from_wav(path) + pause
            merged.export(part_path, format="mp3")
            total_ms += len(merged)
            part_paths.append(part_path)
        except Exception:
            failed_parts.append(gi)

    full_path = None
    if part_paths and total_ms <= FULL_MERGE_MAX_SECONDS * 1000:
        merged = AudioSegment.empty()
        for p in part_paths:
            merged += AudioSegment.from_mp3(p)
        full_path = os.path.join(out_dir, "FULL_DOCUMENT.mp3")
        merged.export(full_path, format="mp3")

    return {"parts": part_paths, "full": full_path, "failed_parts": failed_parts}


def _describe_error(e):
    msg = str(e)
    return (
        f"A speech batch failed: {msg}. Your progress is saved - click "
        "Continue to retry. If this keeps happening, the app may be low "
        "on memory - try rebooting it from 'Manage app', or switch to "
        "the Online voice engine, which uses almost no local RAM."
    )


# ------------------------------------------------------- ONLINE (no local model)

def blocks_to_speech_text(blocks):
    return [b[1] for b in blocks if b[1].strip()]


def generate_speech_online(blocks, lang, out_dir, progress_cb=None):
    """The Microsoft-voice path: no local model, so no RAM competition
    with translation - a simple loop, not job/resume based, since a
    memory crash isn't really a risk here."""
    if not _ffmpeg_available():
        return {
            "parts": [], "full": None, "failed_parts": [],
            "error": (
                "FFmpeg isn't available on this server, so audio can't be "
                "converted to MP3. Check that packages.txt (containing "
                "'ffmpeg') is at the root of your GitHub repository."
            ),
        }
    import asyncio
    import edge_tts

    os.makedirs(out_dir, exist_ok=True)
    texts = blocks_to_speech_text(blocks)
    groups, current, wc = [], [], 0
    for t in texts:
        words = len(t.split())
        if current and wc + words > WORDS_PER_PART:
            groups.append(current)
            current, wc = [], 0
        current.append(t)
        wc += words
    if current:
        groups.append(current)

    part_paths, failed_parts, total_ms = [], [], 0
    for idx, group_texts in enumerate(groups):
        if progress_cb:
            progress_cb(idx, len(groups))
        joined = "\n".join(group_texts)
        part_path = os.path.join(out_dir, f"Part {idx + 1:02d}.mp3")
        tmp_path = os.path.join(out_dir, f"_online_part{idx}.mp3")
        try:
            async def _run():
                communicate = edge_tts.Communicate(joined, EDGE_VOICES[lang])
                await communicate.save(tmp_path)
            asyncio.run(_run())
            seg = AudioSegment.from_file(tmp_path)
            seg.export(part_path, format="mp3")
            os.remove(tmp_path)
            total_ms += len(seg)
            part_paths.append(part_path)
        except Exception:
            failed_parts.append(idx)

    full_path = None
    if part_paths and total_ms <= FULL_MERGE_MAX_SECONDS * 1000:
        merged = AudioSegment.empty()
        for p in part_paths:
            merged += AudioSegment.from_mp3(p)
        full_path = os.path.join(out_dir, "FULL_DOCUMENT.mp3")
        merged.export(full_path, format="mp3")

    if progress_cb:
        progress_cb(len(groups), len(groups))

    return {"parts": part_paths, "full": full_path, "failed_parts": failed_parts, "error": None}
