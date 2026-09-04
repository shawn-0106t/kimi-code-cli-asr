# kimi-code-cli-asr

[中文说明](README_CN.md)

Transcribe local meeting recordings to text inside Kimi Code CLI, powered by the Qwen AI platform Token Plan ASR model (`qwen-audio-3.0-asr-flash`) — one sentence triggers the whole pipeline: auto-slicing → concurrent transcription → timeline-merged transcript → meeting minutes in the same session. All calls stay within the Token Plan bundle; no extra spend.

## Features

- **Long-audio pipeline**: recordings of 30–120 min are auto-sliced (default 240s/slice, under the model's 5-min limit), transcribed with 3-way concurrency, and merged back on the timeline
- **Resume without re-billing**: slice-level cache (audio SHA1 + slice index + context hash); interrupted runs only redo failed slices
- **Hotword context enhancement**: brand/platform/client names from `skill/meeting-asr/assets/hotwords.txt` are injected as conversation context to improve proper-noun recognition
- **Cost transparency**: every run prints audio duration, new requests, cache hits, and billed seconds (`usage.duration`)
- **Privacy-safe**: Base64 direct upload to the token-plan endpoint only, no third-party hosting; API key from env var or Windows user registry, never written to disk; first run requires explicit `--consent`
- **Kimi Code skill**: a self-contained, redistributable skill package (`meeting-asr.skill` / `.zip`) for natural-language triggering

## Quickstart

Prerequisites: Python 3.10+, ffmpeg (`winget install Gyan.FFmpeg` on Windows), a Token Plan key (`sk-sp-...`).

```bash
pip install -r requirements.txt
setx QWEN_TOKEN_PLAN_KEY "sk-sp-..."   # Windows persistent; or export in your shell

# transcribe (first run asks for cloud-processing consent)
PYTHONUTF8=1 python skill/meeting-asr/scripts/meeting_asr.py "path/to/meeting.m4a" --consent
```

Outputs next to the audio: `<name>.transcript.md` (with `[mm:ss]` markers) and `<name>.transcript.json` (structured slices + usage). Re-running the same command resumes from cache at zero cost.

Interrupted? Just re-run — completed slices are served from cache. A per-file process lock prevents concurrent double billing.

## Skill form (recommended for Kimi Code users)

The skill ships in this repo at `skill/meeting-asr/` (self-contained: `SKILL.md` + `scripts/` + `assets/` + `references/`). Install by copying it to `~/.kimi-code/skills/`:

```bash
cp -r skill/meeting-asr ~/.kimi-code/skills/
```

Once installed, just say "把这段会议录音转成文字" — no commands needed. Feishu/Lark Minutes recordings are out of scope (use the lark-meeting skill instead).

## Docs

| File | Contents |
|---|---|
| [`asr-integration-prd-spec.md`](asr-integration-prd-spec.md) / [`_EN`](asr-integration-prd-spec_EN.md) | PRD + SPEC + implementation log (milestones, verification records) |
| [`AGENTS.md`](AGENTS.md) | Bilingual guidance for AI agents working in this repo |
| [`README_CN.md`](README_CN.md) | 中文说明 |

## Not committed

`.gitignore` excludes raw audio (`testdata/`, `*.wav/mp3/m4a/...`), slice cache (`cache/`), transcripts (`*.transcript.*`), and credentials.

## License

[MIT](LICENSE)
