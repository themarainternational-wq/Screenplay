# Screenwriter Assistant (Streamlit edition)

A private, ₹0 tool that translates Tamil ↔ English screenplays and reads
them aloud, built for very large documents (hundreds of pages) - hosted
permanently on **Streamlit Community Cloud**, with a real public URL you
open on your iPhone. **No card, no subscription, no keeping your own
computer running.**

---

## Why this version looks different from the Hugging Face one

I built an earlier version of this for Hugging Face Spaces with ZeroGPU
hardware. You confirmed your Hugging Face account can't create a Space at
all without a paid plan, so that path is closed. This version is a real
redesign for Streamlit Community Cloud, not a re-skin:

- **No GPU exists here at all** (ZeroGPU's `@spaces.GPU`, its CUDA
  placement rules, all of it - gone, and correctly so, since it doesn't
  apply to this host).
- **The RAM budget is much smaller.** Streamlit Community Cloud gives
  roughly **1GB of RAM for the entire app** (confirmed from Streamlit's
  own documentation - not the ~2.7GB figure floating around; I'd rather
  correct that now than have the app crash on day one). NLLB-200
  (600M parameters, ~2.4GB just for its weights) was never going to fit
  here - it's swapped for much smaller models, explained below.
- **The processing/job system is the same idea, rebuilt for Streamlit.**
  ZeroGPU's limit was "5 minutes of GPU time per day" - Streamlit's limit
  is "roughly 1GB of RAM, always." So instead of pausing for a daily
  quota, this version processes in small batches bounded by *time* (a
  few seconds per step) and *memory* (nothing ever holds a whole document
  in RAM at once), auto-continuing on its own while your tab is open, and
  resumable by Job ID if you close it or the app goes to sleep.

---

## The models, and why

| Task | Model | Why |
|---|---|---|
| Tamil → English | `Helsinki-NLP/opus-mt-dra-en` | ~75M params (~100-150MB after int8 quantization) vs NLLB's 600M/~2.4GB |
| English → Tamil | `Helsinki-NLP/opus-mt-en-dra` (with the `>>tam<<` target token) | Same size class, same reasoning |
| Speech (local) | `facebook/mms-tts-tam` / `facebook/mms-tts-eng` | Already small (~36M params each); unchanged from before |
| Speech (online, optional) | Microsoft Edge free voices via `edge-tts` | Uses almost none of this app's own RAM - useful headroom if translating and narrating in the same session |

Every model is loaded once (`st.cache_resource`, so it's not re-downloaded
per visitor) and **dynamically quantized to 8-bit** right after loading -
a standard, CPU-native technique that typically cuts a transformer
model's memory by 2-4x with a small, usually-not-noticeable quality cost.

**Honest quality note:** the translation models are trained across the
whole Dravidian language family, not Tamil-specifically, unlike NLLB or
IndicTrans2. That's a real trade for fitting in ~1GB, not a hidden one.
I also could not live-test the exact `Helsinki-NLP/opus-mt-en-dra` +
`>>tam<<` combination against the real Hugging Face Hub from where I
built this (no network access to it from my sandbox) - the `>>tam<<`
prefix is OPUS-MT's standard, documented convention for their
one-to-many models, verified by convention, not by a live run. If Tamil
output looks off after you deploy, that prefix is the first thing to
check.

---

## Deployment - exact steps for a non-programmer

### Part 1: Put the code on GitHub

1. Go to **github.com**, tap **Sign up** if you don't have an account
   (free, no card).
2. Tap the **+** icon (top right) → **New repository**.
3. Name it `screenwriter-assistant`, keep it **Public** (Streamlit
   Community Cloud requires a public repo on the free tier), tap
   **Create repository**.
4. On the new repo's page, click **uploading an existing file** (a link
   in the middle of the page).
5. Drag in **every file and folder from this project**, keeping the
   exact structure: `app.py`, `requirements.txt`, `packages.txt`,
   `README.md`, and the whole `core/` folder (7 files inside).
   GitHub's uploader supports dragging whole folders in most browsers;
   if yours doesn't, upload the files inside `core/` one at a time into
   a folder named `core`.
6. Scroll down, tap **Commit changes**.

### Part 2: Deploy it on Streamlit Community Cloud

1. Go to **share.streamlit.io**, tap **Sign up**, choose **Continue
   with GitHub** (free, no card) and authorize it.
2. Tap **Create app** (or **New app**).
3. Choose **"Deploy a public app from GitHub"**.
4. Pick your `screenwriter-assistant` repository, branch `main`, and
   set **Main file path** to `app.py`.
5. Tap **Deploy**.
6. The first build downloads the AI models - **allow 5-10 minutes** for
   the very first startup. You'll see build logs; once it says your app
   is live, it's ready.
7. Your app's address (something like
   `https://screenwriter-assistant-yourname.streamlit.app`) is now
   permanent. Open it on your iPhone in Safari, tap **Share → Add to
   Home Screen** to make it feel like a real app icon.

You do not need to keep your own computer on for any of this - once
deployed, Streamlit's servers run it, and it wakes itself up when
someone visits (after ~12 hours of no visitors, it goes to sleep to
save resources, and the first visitor after that waits about a minute
for it to wake back up - normal, not an error).

---

## Using it

1. Upload your script, pick a direction (or language, for speech), tap
   **Translate** / **Generate speech**.
2. It processes automatically in small batches, updating the progress
   bar on its own while the tab stays open.
3. A **Job ID** is shown throughout. If you close the tab, the app goes
   to sleep, or anything interrupts it, come back (even a different
   device) and paste that Job ID into "Resume with a Job ID" to
   continue exactly where it left off - not from scratch.
4. Once complete, download buttons for DOCX/PDF or the MP3 parts appear
   automatically.

---

## What actually happens to your scripts (privacy)

- **Translation** and **Local-engine speech** both run on open-source
  models on the same server as the app - nothing sent to
  Google/OpenAI/Anthropic/anyone else.
- **Online-engine speech** sends text to Microsoft's free read-aloud
  service. Off by default in spirit (recommended for memory reasons,
  but you can pick Local instead if privacy matters more to you for a
  given document).
- Job progress and generated files live in a temporary folder on the
  app's own disk, not a database - a full app rebuild clears them,
  ordinary use does not.

## Honest limitations (please read)

- **~1GB RAM for the whole app is genuinely tight.** If you translate
  a document *and* generate Local-engine speech for it in the same
  session, you're asking a ~1GB box to hold Streamlit itself, a
  translation model, and a speech model at once. If the app shows a
  memory/resource error, the practical fixes (in order): use the
  Online speech engine instead of Local; process translation and
  speech in separate sessions rather than back-to-back; or reboot the
  app from "Manage app" in the Streamlit dashboard, which clears
  cached models and starts fresh.
- Machine translation and open-source TTS are good, not perfect,
  especially for a general-Dravidian rather than Tamil-specific
  translation model. Treat the output as a strong first draft.
- `packages.txt` (installing ffmpeg) uses Streamlit's own documented
  apt-get mechanism, confirmed against their current docs. Their forums
  do show occasional transient apt-repository hiccups on their end
  unrelated to this project - if MP3 conversion ever fails right after
  a fresh deploy, try **Reboot app** once before assuming something's
  wrong with these files.
- The app processes in small time-boxed batches rather than one giant
  request, specifically to respect the RAM ceiling - very large
  documents will take real wall-clock time, shown honestly via the
  progress bar and Job ID rather than promised as instant.
- `core/chunker.py` is present but currently unused by the batching
  logic (which is built directly into `translate.py`/`tts.py`) - kept
  in case it's useful for a future feature, flagged here so it isn't
  mistaken for something load-bearing.

## What's built vs. what's next

**Built:** upload (PDF/DOCX/TXT, any size) → automatic memory-safe
batching → Tamil↔English translation with consistent character names,
resumable across sleeps/restarts → DOCX/PDF export preserving
screenplay structure → text-to-speech (Tamil & English, local or
online engine) → MP3 (parts + full merge when practical) → Job
ID-based resume from any session → mobile-friendly interface.

**Designed for later, not built:** script analysis (synopsis, scene
breakdown, character list, pacing notes) and "ask my script"
question-answering - both would reuse the same `extract` module and
the same batched-job pattern already here.
