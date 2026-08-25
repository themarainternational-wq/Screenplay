"""
core/translate.py
------------
Tamil <-> English translation using small, CPU-friendly OPUS-MT models,
dynamically quantized to int8 - built to run inside Streamlit Community
Cloud's free tier (~1GB RAM for the WHOLE app, no GPU at all).

WHY NOT NLLB-200 (used in the earlier Hugging Face Spaces version of
this project)?
NLLB-200-distilled-600M needs roughly 2.4GB of RAM just for its weights
in float32 - that alone blows through Streamlit Community Cloud's
entire per-app budget before Streamlit itself, the TTS model, or any
document data is even loaded. It's the wrong model for this host.

WHAT'S USED INSTEAD:
Helsinki-NLP's OPUS-MT Dravidian-language models
(Helsinki-NLP/opus-mt-dra-en for Tamil->English,
 Helsinki-NLP/opus-mt-en-dra for English->Tamil), ~75M parameters each,
about 300MB before quantization and roughly 100-150MB after dynamic
int8 quantization of their linear layers - small enough to coexist
with Streamlit and a TTS model in under 1GB.

HONEST QUALITY NOTE: these are not Tamil-specialized the way NLLB or
IndicTrans2 are - they're trained across the Dravidian language family.
This is a genuine quality-for-memory trade-off, not a hidden one. I
could not live-test the exact model+prefix combination below against
the real Hugging Face Hub from this sandbox (no network access to
huggingface.co here) - the ">>tam<<" target-language prefix is OPUS-MT's
standard, well-documented convention for their one-to-many models, but
flagging that this specific pairing is verified by convention, not by
a live test run.

ARCHITECTURE FOR STREAMLIT:
No GPU, no @spaces.GPU, no quota. The constraint here is RAM and CPU,
not GPU minutes. Large documents are still processed as a resumable
JOB (same on-disk persistence as before) so a very long document
doesn't have to sit in memory at once and progress survives the app
sleeping or a dropped connection - but chunks are now bounded by a
wall-clock time budget per processing "leg" rather than a GPU-session
duration, since there's no scarce daily quota to design around here.
"""

import re
import time

import streamlit as st
import torch

from core import jobstore

TA_EN_MODEL = "Helsinki-NLP/opus-mt-dra-en"
EN_TA_MODEL = "Helsinki-NLP/opus-mt-en-dra"
EN_TA_PREFIX = ">>tam<<"  # OPUS-MT's standard target-language token for Tamil

BATCH_SIZE = 8                  # texts per single model.generate() call
CHUNK_SECONDS_BUDGET = 8        # wall-clock budget per processing "leg"
MAX_INPUT_CHARS = 400


@st.cache_resource(show_spinner="Loading translation model (first time only)...")
def _load_model(direction):
    """Cached for the life of the app process - loaded once, reused by
    every user/session, not re-downloaded or re-loaded per request."""
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    model_id = TA_EN_MODEL if direction == "ta2en" else EN_TA_MODEL
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
    model.eval()
    # Dynamic int8 quantization of the linear layers - CPU-native (no
    # CUDA/bitsandbytes needed), typically cuts memory ~2-4x for
    # transformer models since most parameters live in linear layers.
    model = torch.quantization.quantize_dynamic(
        model, {torch.nn.Linear}, dtype=torch.qint8
    )
    return model, tokenizer


def _translate_batch_raw(texts, src, tgt):
    direction = "ta2en" if (src, tgt) == ("ta", "en") else "en2ta"
    model, tokenizer = _load_model(direction)
    trimmed = [t[:MAX_INPUT_CHARS] for t in texts]
    if direction == "en2ta":
        trimmed = [f"{EN_TA_PREFIX} {t}" for t in trimmed]
    inputs = tokenizer(
        trimmed, return_tensors="pt", padding=True, truncation=True, max_length=512
    )
    with torch.no_grad():
        output = model.generate(**inputs, max_length=512, num_beams=4)
    return tokenizer.batch_decode(output, skip_special_tokens=True)


class NameLocker:
    """Collects character names from CHARACTER blocks and locks each to
    ONE translated form, applied everywhere via placeholder swapping -
    so "Ravi" (or "RAVI" in a cue line) never ends up with three
    different spellings across a long document."""

    def __init__(self, blocks):
        self.names = []
        seen = set()
        for btype, text in blocks:
            if btype == "CHARACTER":
                base = re.sub(r"\(.*?\)", "", text).strip()
                if base and base not in seen:
                    seen.add(base)
                    self.names.append(base)
        self.names.sort(key=len, reverse=True)
        self._translated_cache = {}

        if self.names:
            pattern = "|".join(re.escape(n) for n in self.names)
            self._name_re = re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
        else:
            self._name_re = None

    def _canonical(self, matched_text):
        low = matched_text.lower()
        for n in self.names:
            if n.lower() == low:
                return n
        return matched_text

    def protect(self, text):
        if not self._name_re:
            return text, {}
        placeholders = {}
        counter = [0]

        def repl(m):
            canonical = self._canonical(m.group(0))
            token = f"\uE000{counter[0]}\uE000"
            counter[0] += 1
            placeholders[token] = canonical
            return token

        out = self._name_re.sub(repl, text)
        return out, placeholders

    def restore(self, text, placeholders):
        out = text
        for token, name in placeholders.items():
            out = out.replace(token, self._translated_cache.get(name, name))
        return out


def _prepare_job_state(blocks, src, tgt, base="document"):
    return {
        "kind": "translate",
        "src": src,
        "tgt": tgt,
        "base": base,
        "blocks": [[b[0], b[1]] for b in blocks],
        "total": len(blocks),
        "translated": [None] * len(blocks),
        "done": [False] * len(blocks),
        "names_translated": False,
        "name_cache": {},
        "last_error": None,
    }


def start_or_get_job(job_id, blocks, src, tgt, base="document"):
    state = jobstore.load(job_id)
    if state is None:
        state = _prepare_job_state(blocks, src, tgt, base)
        jobstore.save(job_id, state)
    return state


def run_translate_job(job_id, time_budget=CHUNK_SECONDS_BUDGET, progress_cb=None):
    """
    Advances an existing job for up to `time_budget` seconds of actual
    work, then returns (state, complete, stopped_reason). Safe to call
    repeatedly - each call resumes exactly where the last one left off
    (from disk), whether that's a second later (auto-continue) or days
    later (a pasted-in Job ID after the app slept).
    """
    state = jobstore.load(job_id)
    if state is None:
        raise ValueError(f"No such job: {job_id}")

    blocks = [(b[0], b[1]) for b in state["blocks"]]
    locker = NameLocker(blocks)

    for i, (btype, text) in enumerate(blocks):
        if not text.strip() and not state["done"][i]:
            state["translated"][i] = text
            state["done"][i] = True

    if locker.names and not state["names_translated"]:
        try:
            name_results = _translate_batch_raw(locker.names, state["src"], state["tgt"])
            for name, translated in zip(locker.names, name_results):
                state["name_cache"][name] = translated
            state["names_translated"] = True
            jobstore.save(job_id, state)
        except Exception as e:
            state["last_error"] = _describe_error(e)
            jobstore.save(job_id, state)
            return state, False, state["last_error"]
    locker._translated_cache = state["name_cache"]

    pending = [i for i in range(len(blocks)) if not state["done"][i]]
    if not pending:
        return state, True, None

    prepared = []
    for i in pending:
        _, text = blocks[i]
        protected, placeholders = locker.protect(text)
        prepared.append((i, protected, placeholders))
    batches = [prepared[k:k + BATCH_SIZE] for k in range(0, len(prepared), BATCH_SIZE)]

    stopped_reason = None
    start_time = time.time()
    processed_any = False
    for batch in batches:
        if processed_any and (time.time() - start_time) > time_budget:
            break
        texts = [item[1] for item in batch]
        try:
            results = _translate_batch_raw(texts, state["src"], state["tgt"])
        except Exception as e:
            stopped_reason = _describe_error(e)
            state["last_error"] = stopped_reason
            jobstore.save(job_id, state)
            break

        for (i, _protected, placeholders), translated_text in zip(batch, results):
            state["translated"][i] = locker.restore(translated_text, placeholders)
            state["done"][i] = True
        processed_any = True

        jobstore.save(job_id, state)
        if progress_cb:
            progress_cb(sum(state["done"]), state["total"])

    complete = all(state["done"])
    return state, complete, stopped_reason


def _describe_error(e):
    msg = str(e)
    return (
        f"A translation batch failed: {msg}. Your progress is saved - "
        "click Continue to retry. If this keeps happening, the app may "
        "be low on memory - try rebooting it from 'Manage app'."
    )
