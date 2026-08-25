"""
Screenwriter Assistant (Streamlit Community Cloud edition)
------------------------------------------------------------
A free, private translation + text-to-speech tool for large Tamil/English
screenplay documents, built to run inside Streamlit Community Cloud's
free tier (~1GB RAM total for the whole app, CPU only, no GPU, no card).

Run locally:      streamlit run app.py
Deploy for free:   push this repo to GitHub, then deploy it on
                    share.streamlit.io - see README.md for exact steps.

Large documents are processed as a resumable JOB: progress is saved to
disk after every small batch, so a long document doesn't need to sit
in memory at once, and nothing is lost if the app sleeps, the
connection drops, or you close the tab - come back (even days later,
even a different device) with the Job ID shown on screen and pick up
exactly where it left off.
"""

import os
import tempfile
import time
import uuid

import streamlit as st

from core.extract import extract
from core import translate as T
from core import tts as TTS
from core import jobstore
from core.docx_export import export_docx
from core.pdf_export import export_pdf

WORKDIR = os.path.join(tempfile.gettempdir(), "screenwriter_assistant")
os.makedirs(WORKDIR, exist_ok=True)

DIRECTIONS = {
    "Tamil → English": ("ta", "en"),
    "English → Tamil": ("en", "ta"),
}

st.set_page_config(page_title="Screenwriter Assistant", page_icon="🎬", layout="centered")


def _session_dir():
    d = os.path.join(WORKDIR, uuid.uuid4().hex[:12])
    os.makedirs(d, exist_ok=True)
    return d


def _basename_no_ext(name):
    return os.path.splitext(os.path.basename(name))[0]


def _save_upload(uploaded_file):
    """Streamlit gives an in-memory file object, not a path - write it
    to a temp path so the existing extract() (path-based) can read it
    unchanged."""
    out_dir = _session_dir()
    path = os.path.join(out_dir, uploaded_file.name)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


def _register_file(path):
    if path and path not in st.session_state.session_files:
        st.session_state.session_files.append(path)


if "session_files" not in st.session_state:
    st.session_state.session_files = []
if "translate_job_id" not in st.session_state:
    st.session_state.translate_job_id = None
if "translate_active" not in st.session_state:
    st.session_state.translate_active = False
if "tts_job_id" not in st.session_state:
    st.session_state.tts_job_id = None
if "tts_active" not in st.session_state:
    st.session_state.tts_active = False

st.title("Screenwriter Assistant")
st.caption(
    "Translate and narrate large Tamil / English scripts — free, private, "
    "no account needed to use."
)

tab_translate, tab_tts, tab_files, tab_about = st.tabs(
    ["Translate", "Text to Speech", "Files (this session)", "About / Privacy"]
)

# ---------------------------------------------------------------- TRANSLATE

with tab_translate:
    st.write(
        "Upload a script (PDF, DOCX or TXT) - it can be hundreds of pages. "
        "It's split into batches automatically and processed in order. "
        "Character names stay consistent throughout."
    )
    t_file = st.file_uploader("Upload document", type=["pdf", "docx", "txt"], key="t_file")
    t_direction = st.radio("Direction", list(DIRECTIONS.keys()), key="t_direction")

    col1, col2 = st.columns([1, 1])
    with col1:
        t_start = st.button("Translate", type="primary", key="t_start_btn")
    with col2:
        t_resume_id = st.text_input("Resume with a Job ID", key="t_resume_id", placeholder="e.g. a1b2c3d4e5f6")
    t_resume = st.button("Resume this job", key="t_resume_btn")

    if t_start:
        if t_file is None:
            st.warning("Please upload a document first.")
        else:
            src, tgt = DIRECTIONS[t_direction]
            path = _save_upload(t_file)
            blocks = extract(path)
            if not blocks:
                st.warning("Couldn't find any readable text in that file.")
            else:
                job_id = jobstore.new_job_id()
                T.start_or_get_job(job_id, blocks, src, tgt, base=_basename_no_ext(t_file.name))
                st.session_state.translate_job_id = job_id
                st.session_state.translate_active = True
                st.rerun()

    if t_resume:
        jid = (t_resume_id or "").strip()
        if jid and jobstore.exists(jid):
            st.session_state.translate_job_id = jid
            st.session_state.translate_active = True
            st.rerun()
        else:
            st.error("No matching job found - check the Job ID and try again.")

    if st.session_state.translate_active and st.session_state.translate_job_id:
        job_id = st.session_state.translate_job_id
        progress_bar = st.progress(0.0)
        status = st.empty()

        def _cb(done, total):
            progress_bar.progress(done / max(total, 1))
            status.markdown(f"Translated **{done} of {total}** blocks...")

        state, complete, stopped_reason = T.run_translate_job(job_id, progress_cb=_cb)

        if complete:
            translated_blocks = [
                (b[0], (t if t is not None else b[1]))
                for b, t in zip(state["blocks"], state["translated"])
            ]
            out_dir = _session_dir()
            base = state.get("base", "document")
            docx_path = os.path.join(out_dir, f"{base} - Translation.docx")
            pdf_path = os.path.join(out_dir, f"{base} - Translation.pdf")
            export_docx(translated_blocks, docx_path, title=f"{base} (translated)")
            export_pdf(translated_blocks, pdf_path, title=f"{base} (translated)")
            _register_file(docx_path)
            _register_file(pdf_path)
            st.session_state.translate_active = False
            st.success(f"✅ Done. Translated all {state['total']} blocks.  \nJob ID: `{job_id}`")
            with open(docx_path, "rb") as f:
                st.download_button("Download DOCX", f, file_name=os.path.basename(docx_path))
            with open(pdf_path, "rb") as f:
                st.download_button("Download PDF", f, file_name=os.path.basename(pdf_path))
        elif stopped_reason:
            st.session_state.translate_active = False
            done_count = sum(state["done"])
            st.warning(f"⏸ {stopped_reason}")
            st.info(
                f"Progress so far: {done_count} of {state['total']} blocks.  \n"
                f"Job ID: `{job_id}` — click **Resume this job** above (paste the "
                f"Job ID) whenever you want to continue."
            )
        else:
            done_count = sum(state["done"])
            status.markdown(
                f"Translated **{done_count} of {state['total']}** blocks — "
                f"continuing automatically while this tab stays open. "
                f"Job ID: `{job_id}`"
            )
            time.sleep(0.3)
            st.rerun()

# --------------------------------------------------------------- TEXT TO SPEECH

with tab_tts:
    st.write(
        "Upload a script and it will be read aloud, start to finish. The "
        "Local engine runs entirely on this server (private, but shares "
        "this app's tight free-tier memory with translation). The Online "
        "engine sends text to Microsoft's free voice service (not private, "
        "but uses almost none of this app's own memory - the safer choice "
        "if you're also translating in the same session)."
    )
    s_file = st.file_uploader("Upload document", type=["pdf", "docx", "txt"], key="s_file")
    s_lang = st.radio("Language", ["Tamil", "English"], key="s_lang")
    s_engine = st.radio(
        "Voice engine",
        ["Online (Microsoft neural voice, recommended, not private)",
         "Local (private, uses this app's own limited RAM)"],
        key="s_engine",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        s_start = st.button("Generate speech", type="primary", key="s_start_btn")
    with col2:
        s_resume_id = st.text_input("Resume with a Job ID", key="s_resume_id", placeholder="e.g. a1b2c3d4e5f6")
    s_resume = st.button("Resume this job", key="s_resume_btn")

    if s_start:
        if s_file is None:
            st.warning("Please upload a document first.")
        else:
            lang = "ta" if s_lang == "Tamil" else "en"
            path = _save_upload(s_file)
            blocks = extract(path)
            if not blocks:
                st.warning("Couldn't find any readable text in that file.")
            elif s_engine.startswith("Online"):
                out_dir = _session_dir()
                progress_bar = st.progress(0.0)
                status = st.empty()

                def _cb_online(done, total):
                    progress_bar.progress(done / max(total, 1))
                    status.markdown(f"Generating audio - part {done} of {total}...")

                result = TTS.generate_speech_online(blocks, lang, out_dir, progress_cb=_cb_online)
                if result.get("error"):
                    st.error(f"⚠ {result['error']}")
                else:
                    for p in result["parts"]:
                        _register_file(p)
                    msg = f"✅ Done. Generated {len(result['parts'])} part(s)."
                    if result["failed_parts"]:
                        msg += f" ⚠ {len(result['failed_parts'])} part(s) failed."
                    if result["full"]:
                        msg += " A merged FULL_DOCUMENT.mp3 is also ready."
                        _register_file(result["full"])
                    st.success(msg)
                    if result["full"]:
                        with open(result["full"], "rb") as f:
                            st.download_button("Download full MP3", f, file_name="FULL_DOCUMENT.mp3")
                    for p in result["parts"]:
                        with open(p, "rb") as f:
                            st.download_button(f"Download {os.path.basename(p)}", f, file_name=os.path.basename(p))
            else:
                out_dir = _session_dir()
                job_id = jobstore.new_job_id()
                TTS.start_or_get_tts_job(job_id, blocks, lang, out_dir)
                st.session_state.tts_job_id = job_id
                st.session_state.tts_active = True
                st.rerun()

    if s_resume:
        jid = (s_resume_id or "").strip()
        if jid and jobstore.exists(jid):
            st.session_state.tts_job_id = jid
            st.session_state.tts_active = True
            st.rerun()
        else:
            st.error("No matching job found - check the Job ID and try again.")

    if st.session_state.tts_active and st.session_state.tts_job_id:
        job_id = st.session_state.tts_job_id
        progress_bar = st.progress(0.0)
        status = st.empty()

        def _cb(done, total):
            progress_bar.progress(done / max(total, 1))
            status.markdown(f"Synthesized **{done} of {total}** pieces...")

        state, complete, stopped_reason = TTS.run_tts_job(job_id, progress_cb=_cb)
        total = len(state.get("pieces", []))
        done_count = sum(state.get("done", []))

        if complete:
            result = state["result"] or {"parts": [], "full": None, "failed_parts": []}
            for p in result["parts"]:
                _register_file(p)
            st.session_state.tts_active = False
            msg = f"✅ Done. Generated {len(result['parts'])} part(s)."
            if result["failed_parts"]:
                msg += f" ⚠ {len(result['failed_parts'])} part(s) had assembly problems."
            if result["full"]:
                msg += " A merged FULL_DOCUMENT.mp3 is also ready."
                _register_file(result["full"])
            msg += f"  \nJob ID: `{job_id}`"
            st.success(msg)
            if result["full"]:
                with open(result["full"], "rb") as f:
                    st.download_button("Download full MP3", f, file_name="FULL_DOCUMENT.mp3")
            for p in result["parts"]:
                with open(p, "rb") as f:
                    st.download_button(f"Download {os.path.basename(p)}", f, file_name=os.path.basename(p))
        elif stopped_reason:
            st.session_state.tts_active = False
            st.warning(f"⏸ {stopped_reason}")
            st.info(
                f"Progress so far: {done_count} of {total} pieces.  \n"
                f"Job ID: `{job_id}` — click **Resume this job** above whenever "
                f"you want to continue."
            )
        else:
            status.markdown(
                f"Synthesized **{done_count} of {total}** pieces — continuing "
                f"automatically while this tab stays open. Job ID: `{job_id}`"
            )
            time.sleep(0.3)
            st.rerun()

# --------------------------------------------------------------------- FILES

with tab_files:
    st.write(
        "Everything you've generated in this browser session. **This list "
        "is not saved between visits** - a persistent, cross-device file "
        "library would need a paid database, which goes against the ₹0 "
        "goal. Download anything you want to keep."
    )
    if not st.session_state.session_files:
        st.caption("Nothing generated yet this session.")
    else:
        for path in st.session_state.session_files:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    st.download_button(os.path.basename(path), f, file_name=os.path.basename(path), key=f"dl_{path}")

# --------------------------------------------------------------------- ABOUT

with tab_about:
    st.markdown(
        """
### How this works
- **Translation** uses small, open-source OPUS-MT models. **Speech
  (Local engine)** uses an open-source model (MMS-TTS). Both run on the
  same server as this app - your script text does not leave the server
  for either.
- **Speech (Online engine)** sends text to Microsoft's free read-aloud
  service for higher-quality narration and to keep this app's own
  memory free for translation.
- This app runs on Streamlit Community Cloud's free tier: **no GPU,
  roughly 1GB of RAM shared by the whole app.** Large documents are
  processed as a resumable job so nothing needs to fit in memory all at
  once - progress is saved to disk after every small batch. If the app
  restarts or goes to sleep (Streamlit sleeps apps after ~12 hours with
  no visitors), your **Job ID** lets you pick up exactly where you left
  off, from any device.

### Honest limits
- These are deliberately small, CPU-friendly models chosen to fit a
  ~1GB free server, not the largest/best available. Machine translation
  and open-source TTS are good, not perfect - review important scenes
  before relying on the output.
- The Tamil↔English translation models are trained across the
  Dravidian language family rather than Tamil-specifically - a real
  quality-for-memory trade-off, not a hidden one.
- The "Files" tab and Job IDs live in a temporary folder on the app's
  own disk, not a database - a full app rebuild clears them; ordinary
  use (closing a tab, the app sleeping) does not.
                """
    )
