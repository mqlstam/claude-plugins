---
name: notule
description: >-
  Transcribe a meeting recording (video or audio) locally with Whisper/MLX and turn
  it into a Dutch notule, exported as PDF + DOCX with a compressed, emailable audio
  file, all in one dedicated folder. Use when the user shares a meeting recording
  (.mp4/.m4a/.mov/.wav/.mp3) and wants a transcript, meeting minutes / notulen, or
  says "do the same" for another recording.
---

# Notule — meeting recording → Dutch notulen (PDF + DOCX + audio)

For one recording, produce a **dedicated folder** containing everything the user can
attach to an email:

- **PDF** and **DOCX** of the notule
- a **compressed mono audio** file (~24 MB/hour — fits a 25 MB mail limit)
- the raw **transcript** (`.txt` + `.srt`)

Runs **fully locally** on the Apple-Silicon GPU — nothing is uploaded.

## Output folder convention

`~/Endoxia/Notulen/<YYYY-MM-DD> <short title>/` containing the files below. That
`Notulen/` tree is **gitignored** in the Endoxia repo (`/Notulen/` in `.gitignore`),
so the notes live with the project without being committed. For non-Endoxia recordings,
use `~/Downloads/Notulen/` or wherever the user prefers.

- `Notule <title> - <d mmm yyyy>.pdf`
- `Notule <title> - <d mmm yyyy>.docx`
- `Notule <title> - <d mmm yyyy>.md`  (source)
- `meeting-<YYYY-MM-DD>.m4a`
- `transcript-<YYYY-MM-DD>.txt` / `.srt`

## Prerequisites (create if missing)

- **ffmpeg** — `which ffmpeg` || `brew install ffmpeg`
- **pandoc** — `which pandoc` || `brew install pandoc` (Markdown → DOCX, no LaTeX needed)
- **Google Chrome** — used headless for the PDF (no LaTeX/wkhtmltopdf needed)
- **mlx-whisper venv** at `~/.venvs/mlx-whisper`. If absent:
  ```bash
  /opt/homebrew/opt/python@3.11/bin/python3.11 -m venv ~/.venvs/mlx-whisper
  ~/.venvs/mlx-whisper/bin/pip install -q --upgrade pip mlx-whisper
  ```
  Model `mlx-community/whisper-large-v3-mlx` downloads on first run (~3 GB, then cached).

> **Why mlx-whisper, not the `whisper` CLI:** on Apple Silicon the OpenAI `whisper`
> CLI runs on CPU (~1× real-time) and its `--device mps` path is broken
> (`SparseMPS` op `aten::empty.memory_format` has no Metal kernel, no fallback).
> mlx-whisper runs natively on the GPU (~6–19× real-time).

## Pipeline

Resolve a few variables first. The date is usually in the recording filename; ask the
user for a short title (and confirm attendees/roles) if not obvious.

```bash
SRC="/path/to/recording.mp4"          # the recording
DATE="2026-06-06"                      # meeting date (YYYY-MM-DD)
TITLE="Endoxia teamoverleg"            # short title
DIR="$HOME/Endoxia/Notulen/$DATE $TITLE"   # gitignored; or ~/Downloads/Notulen for non-Endoxia
ASSETS="${CLAUDE_PLUGIN_ROOT}/skills/notule/assets"   # transcribe.py + notule-style.css
mkdir -p "$DIR"
```

### 1. Extract audio

```bash
# 16 kHz mono WAV for Whisper
ffmpeg -y -i "$SRC" -vn -ac 1 -ar 16000 -c:a pcm_s16le "/tmp/notule-$DATE.wav"
# compressed, emailable mono audio for the folder (~24 MB/hr)
ffmpeg -y -i "/tmp/notule-$DATE.wav" -c:a aac -b:a 32k -ac 1 "$DIR/meeting-$DATE.m4a"
```

### 2. (recommended) Confirm language on a 90 s sample

```bash
ffmpeg -y -ss 300 -t 90 -i "/tmp/notule-$DATE.wav" -c:a pcm_s16le /tmp/notule-sample.wav
~/.venvs/mlx-whisper/bin/python "$ASSETS/transcribe.py" /tmp/notule-sample.wav /tmp/notule-sample auto
```

Note the detected language (`nl`, `en`, …) for step 3. Skip if you already know it.

### 3. Transcribe (anti-repetition)

```bash
~/.venvs/mlx-whisper/bin/python "$ASSETS/transcribe.py" \
  "/tmp/notule-$DATE.wav" "/tmp/notule-$DATE" nl      # or the detected language
cp "/tmp/notule-$DATE.txt" "$DIR/transcript-$DATE.txt"
cp "/tmp/notule-$DATE.srt" "$DIR/transcript-$DATE.srt"
```

Throughput is ~6–19× real-time. If the audio is longer than ~10 minutes the run
exceeds a single foreground call — **run it in the background** and continue when it
finishes.

`assets/transcribe.py` forces `condition_on_previous_text=False` **and** a temperature
fallback ladder. **This is essential.** The plain `mlx_whisper` / `whisper` CLI uses a
single `--temperature 0` with condition-on-previous-text enabled, which gets stuck in a
hallucination **death-spiral** — one sentence repeated hundreds of times — silently
losing most of the meeting. (Observed live: a 36-min recording lost ~23 min this way.)

### 4. Verify — no hallucination loop (do not skip)

```bash
~/.venvs/mlx-whisper/bin/python -c "import json,collections;d=json.load(open('/tmp/notule-$DATE.json'));print(collections.Counter(s['text'].strip() for s in d['segments']).most_common(5))"
```

Top repeats should be short backchannel (`Ja.`, `Oké.`). If ANY non-trivial sentence
repeats dozens/hundreds of times, the loop hit despite the mitigation — re-run step 3;
if it recurs, chunk the audio around the offending timestamp (find it in the `.srt`)
and transcribe the chunks separately.

### 5. Write the notule (Dutch)

Read the transcript and write `"$DIR/Notule <title> - <d mmm yyyy>.md"`. Structure:

- `# Notulen — <title>`
- **Datum**, **Aanwezig** (names + roles), **Duur**, **Onderwerp**
- A one-line blockquote caveat: owners/deadlines are a proposal to confirm
- Numbered topic sections covering the actual discussion
- `## Besluiten` — the decisions
- `## Actiepunten` — a Markdown table: `| # | Actie | Eigenaar | Deadline |`
- A closing line with the next milestone(s)

**Speaker attribution:** Whisper has no diarization. Infer owners from context and
present them as a proposal. Default Endoxia team & roles (override if the user says
otherwise):

- **Emilio** — bestuurder (roadmap, presentatie, positionering)
- **Miquel** — developer (techniek, demo, build)
- **Luciano** — legal (juridische inhoud, M&A)

**Tone:** professional, factual Dutch, confident understatement. No inspirational
filler ("by design", eyebrows), no emojis. Capture sensitive/internal points in their
legitimate form (e.g. "toon de gezaghebbende eindbron", not "fake it").

### 6. Export PDF + DOCX

```bash
MD="$DIR/Notule <title> - <d mmm yyyy>.md"
pandoc "$MD" -f markdown -o "${MD%.md}.docx"
pandoc "$MD" -f markdown -s --embed-resources -c "$ASSETS/notule-style.css" -o /tmp/notule-render.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="${MD%.md}.pdf" "file:///tmp/notule-render.html"
```

Verify the PDF is non-empty (`ls -lah`, or `mdls -name kMDItemNumberOfPages`).

### 7. Report

Show the folder path and the three email-ready files (PDF, DOCX, m4a). Offer a meeting
summary. For long recordings (>~45 min) the m4a may exceed 25 MB — drop to `-b:a 24k`
or tell the user a share link is needed.

## Notes

- `assets/transcribe.py` args: `<audio> <out-base> [language|auto]` (default `nl`); writes `.txt/.srt/.json`.
- `assets/notule-style.css` is the print stylesheet (paper/ink, one crimson accent rule).
- If `${CLAUDE_PLUGIN_ROOT}` is unset, the assets are in this skill's `assets/` directory — resolve that path and use it for `$ASSETS`.
