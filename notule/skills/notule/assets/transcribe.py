#!/usr/bin/env python3
"""Transcribe with anti-repetition settings (fixes the Whisper death-spiral loop)."""
import json
import sys
import mlx_whisper

AUDIO = sys.argv[1]
OUT = sys.argv[2]
LANG = sys.argv[3] if len(sys.argv) > 3 else "nl"  # "auto" -> language detection
MODEL = "mlx-community/whisper-large-v3-mlx"


def ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


result = mlx_whisper.transcribe(
    AUDIO,
    path_or_hf_repo=MODEL,
    language=(None if LANG == "auto" else LANG),
    temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),  # fallback ladder — escapes bad decodes
    condition_on_previous_text=False,             # breaks the repetition feedback loop
    compression_ratio_threshold=2.4,
    logprob_threshold=-1.0,
    no_speech_threshold=0.6,
    verbose=False,
)

# Write raw JSON first (most important — never lose the result)
with open(OUT + ".json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

segs = result.get("segments", [])

with open(OUT + ".txt", "w", encoding="utf-8") as f:
    f.write(result["text"].strip() + "\n")

with open(OUT + ".srt", "w", encoding="utf-8") as f:
    for i, seg in enumerate(segs, 1):
        f.write(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text'].strip()}\n\n")

print("DONE | segments:", len(segs), "| language:", result.get("language"), "| chars:", len(result["text"]))
