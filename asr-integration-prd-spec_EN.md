# Integrating Qwen AI Platform Token Plan (ASR) into Kimi Code CLI — PRD / SPEC / Implementation Log

> Version: v1.0  Date: 2026-09-05
> Change log:
> - v0.2: Confirmed the token plan is the Qwen AI Platform Token Plan (DashScope-compatible, `sk-sp-` Key); selected filetrans
> - v0.3: WebBridge explored 2 pages; confirmed the plan includes only `qwen-audio-3.0-asr-flash` (synchronous, ≤5 minutes); rewrote the model selection and architecture
> - v0.4: WebBridge explored 2 more pages (OpenAI-compatible endpoint, temporary URL upload); clarified API boundaries, added an upload fallback plan, revised the slice bitrate strategy
> - v0.5: Added section 2.9 tech stack review conclusion — the review confirmed keeping the current tech stack (Python + requests + ffmpeg CLI + Skill form); the dashscope SDK route is kept on record but not adopted
> - v1.0: M1~M3 implementation complete; the original `asr-implementation-plan.md` was retired, and its milestone conclusions and quality records were merged into Part 3
> Positioning: a three-in-one document — PRD (why and what), SPEC (how), and implementation log (how it went)

---

## Part 0 · Research Findings (including WebBridge hands-on results)

### 0.1 GitHub / Tech Community

No ready-made solution exists for "Kimi Code CLI + ASR token plan" — only adjacent components (the Kimi-Audio open-source model, whisper-mcp, ffvoice-engine, etc.). The Kimi API platform has no public audio transcription endpoint. Conclusion: a custom design is required.

### 0.2 Official Documentation Hands-on (2026-09, read via Kimi WebBridge in the user's real browser, 4 pages total)

**Page 1: [Using Token Plan to access multimodal generation models](https://platform.qianwenai.com/docs/token-plan/best-practices/multimodal-generation)**

- Contains only three integration examples — text-to-image, text-to-video, and TTS — **no ASR example**
- Confirmed the integration pattern: Skill / Slash Command form + `sk-sp-` Key via environment variable
- token-plan endpoint: `https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

**Page 2: [Speech Recognition · Audio File Transcription](https://platform.qianwenai.com/docs/developer-guides/speech/asr)**

| Fact | Impact |
|---|---|
| `qwen-audio-3.0-asr-flash` (the plan's only ASR model) uses **synchronous calls**, with the same endpoint `services/aigc/multimodal-generation/generation` | Same endpoint path as token-plan text-to-image; very likely to work (verified in M1) |
| Per-request audio limit: **5 minutes** | Long meetings require a self-built slicing pipeline |
| Input supports **URL / Base64** | Local direct upload, no public hosting needed |
| Unusual response structure: `output.text` (and `output.output.sentence.text`), **no choices field** | Parsing logic needs dedicated adaptation |
| **No speaker diarization** (only filetrans supports it) | US-4 downgraded |
| **Context enhancement supported**: hotwords/domain text passed as conversation history in messages (user+assistant pairs), significantly improving proper-noun recognition | The hotwords solution adopts this mechanism |
| Official FAQ: avoid slicing audio too short | Slice granularity 3~5 minutes, no smaller than 1 minute |

**Page 3: [Audio File Recognition (Qwen-ASR) OpenAI Compatible](https://platform.qianwenai.com/docs/api-reference/speech-recognition/qwen-asr/openai)**

| Fact | Impact |
|---|---|
| The OpenAI-compatible endpoint (`/compatible-mode/v1/chat/completions`) **supports only `qwen3-asr-flash`**, not this plan's `qwen-audio-3.0-asr-flash` | This solution **cannot use** the OpenAI-compatible endpoint; only the DashScope-style multimodal-generation endpoint works |
| Base64 uses Data URL format (`data:audio/wav;base64,...`); this model requires **≤10MB** after encoding | Slices must be compressed before transmission (see 2.3) |
| Response `usage` includes `audio_tokens` and `seconds` fields | If this model returns the same, the usage report can take real values instead of estimates (verified in M1) |
| Proprietary parameter `asr_options` (language, enable_itn) passed via extra_body | Only applies to qwen3-asr-flash; not used by this model |

**Page 4: [Upload File to Get Temporary URL](https://platform.qianwenai.com/docs/api-reference/more/upload-file-get-temporary-url)**

| Fact | Impact |
|---|---|
| The platform provides a two-step upload: `GET /api/v1/uploads?action=getPolicy&model=<model>` to get OSS credentials → POST to upload_host → get an `oss://` temporary URL (**valid for 48 hours**) | The **official fallback upload channel** when Base64 fails or the file is too large; no self-owned OSS needed |
| When calling the model with an oss:// URL, the header `X-DashScope-OssResourceResolve: enable` is required; the model parameter of the upload credential must match the called model | Written into the SPEC calling convention |
| The credential endpoint is rate-limited at 100 QPS (per account + model) | No impact on this scenario (once per file) |
| All example base URLs use `dashscope.aliyuncs.com` | Whether the token-plan endpoint supports it too — added to the M1 spike |

> M1 was tested on 2026-09-04; the five conclusions are in 2.8-1: endpoint works, Base64 accepted, usage returns `duration` in seconds, word-level timestamps available, and getPolicy is unavailable on the token-plan endpoint (oss fallback abandoned).

---

## Part 1 · PRD

### 1.1 Background

The user (an advertising media planner) attends many client meetings and internal alignment meetings daily, and afterwards needs to turn recordings (mp3/m4a/wav, mostly Chinese, often mixed Chinese-English, 30~120 minutes) into transcripts and meeting minutes. Pain points: web-based tools break the workflow outside the CLI; local Whisper is slow and poor at Chinese proper nouns; the goal is to close the loop inside Kimi Code CLI while reusing the existing Qwen Token Plan (whose only ASR model is `qwen-audio-3.0-asr-flash`).

### 1.2 Goal

In Kimi Code CLI, convert a local meeting recording into a transcript with a single sentence (or a single command), entirely through the Token Plan's `qwen-audio-3.0-asr-flash`, incurring no out-of-plan cost.

### 1.3 User Stories

- US-1: Place `客户周会-0905.m4a` (client weekly meeting recording, Sep 5) into the working directory, tell Kimi Code "transcribe this meeting recording", and get a transcript with paragraph-level timestamps saved to disk
- US-2: Long recordings of 30~120 minutes are automatically sliced, transcribed segment by segment, and merged along the timeline — no manual splitting
- US-3: After the transcript is generated, directly ask in the same session to "produce meeting minutes based on this transcript"
- US-4 (downgraded): the model does not support speaker diarization; replaced by optional LLM post-processing — after transcription, Kimi infers per-paragraph speakers semantically, labeled as "inferred"
- US-5: Use hotwords/domain context (brand names, media platform names, client names) to improve proper-noun recognition (the official context enhancement mechanism)

### 1.4 Functional Scope (MoSCoW)

| Level | Features |
|---|---|
| Must | Single-file transcription (mp3/m4a/wav/flac etc., unified transcoding via ffmpeg); **automatic slicing + concurrent calls + timeline merge**; Base64 Data URL transmission (fallback to temporary URL upload on failure); slice-level cache for resumable runs; context enhancement (hotwords); results saved to disk (md + JSON); usage report |
| Should | Paragraph-level timestamps (derived from slice offsets); meeting-minutes handoff prompt template |
| Could | LLM-inferred speaker labeling (US-4); batch directory processing; srt export |
| Won't (this phase) | Speaker diarization (model unsupported, unless the plan is upgraded to filetrans); real-time streaming transcription; local models; GUI; audio capture |

### 1.5 Non-functional Requirements

- **Environment**: Windows 10/11 + Git Bash; Python with `PYTHONUTF8=1`; explicit UTF-8 for file I/O; no emoji and no symbols outside the GBK charset in terminal output
- **Privacy**: Base64 direct upload by default, no third-party hosting; when falling back to the temporary URL approach, recordings remain in the official OSS temporary space for 48 hours (private ACL); first use requires explicit confirmation via `--consent`
- **Cost transparency**: print transcription duration, request count, and the usage field returned by the response (if the endpoint returns it) on every run
- **Resumability**: slice-level result cache (audio SHA1 + slice index); rerunning after an interruption only retries failed slices

### 1.6 Acceptance Criteria

1. A 60-minute Chinese meeting m4a, triggered by one sentence/one command, produces a complete merged transcript
2. Output is UTF-8 without mojibake; terminal logs show no abnormal symbols in a GBK console
3. Rerunning after a mid-run network failure does not re-request already successful slices
4. The entire run calls only the token-plan endpoint and uses only `qwen-audio-3.0-asr-flash`

---

## Part 2 · SPEC

### 2.1 Model and Endpoint

| Item | Value |
|---|---|
| Model | `qwen-audio-3.0-asr-flash` (the plan's only ASR model) |
| Endpoint | `POST https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation` (DashScope-style synchronous API; **not** the OpenAI-compatible endpoint, which only supports qwen3-asr-flash) |
| Auth | `Authorization: Bearer $QWEN_TOKEN_PLAN_KEY` (`sk-sp-` prefix, injected via environment variable, never written to disk) |
| Call mode | Synchronous, one call per slice; header `X-DashScope-SSE: disable` |
| Input mode A (default) | Base64 Data URL: `data:audio/mpeg;base64,...` placed in `input_audio.data` |
| Input mode B (fallback) | Temporary URL upload: `GET <base>/api/v1/uploads?action=getPolicy&model=qwen-audio-3.0-asr-flash` → POST to `upload_host` → get an `oss://` URL (valid 48h); add header `X-DashScope-OssResourceResolve: enable` when calling. **M1 test result: getPolicy returns 404 on the token-plan endpoint; the same Key returns 401 InvalidApiKey on the official dashscope domain — the sk-sp- Key does not apply to the dashscope domain, so the oss fallback channel is unavailable for this plan and Base64 is the only input channel** |
| Parameters | `parameters.format`, `parameters.sample_rate` |
| Response parsing | M1 test result: top-level `text` and `output.text` coexist redundantly; prefer `output.text` (fall back to top-level `text`, then `output.sentence.text`); **no choices field**; usage returns `{"duration": <seconds>}` (no audio_tokens/seconds); bonus: `output.sentence.words[]` returns **word-level timestamps** (begin_time/end_time, in milliseconds); `speaker_id` is always null |
| Limits | ≤5 minutes per request; multilingual and dialect support; context enhancement supported; no speaker diarization |

### 2.2 Integration Form (conclusion of three options)

| Dimension | ① Skill + script (recommended) | ② MCP server | ③ Standalone CLI script |
|---|---|---|---|
| Usage | Triggered by one sentence in Kimi Code; the script can also be run manually | Registered as an Agent tool via `mcp.json` | Type commands in the terminal |
| Dependencies | Python 3.10+, `requests`, `ffmpeg`; skill manifest | Same as left + MCP framework + registration | Only Python + requests + ffmpeg |
| Maintainability | High, and the form recommended by the official best practice | Medium — one extra layer of protocol and process lifecycle | High, but no natural-language entry |
| Best for | Daily high-frequency use, transcription → meeting minutes in one continuous flow | Multi-Agent orchestration (not needed this phase) | Occasional/batch use |

**Recommended: ① Skill + script** (the script itself is form ③, usable standalone). Skill source path: `skill/meeting-asr/` (in the repo); installed copy: `~/.kimi-code/skills/meeting-asr/`, with YAML front matter (name/description/trigger words).

### 2.3 Architecture and Transcription Pipeline

```
User: "Transcribe 客户周会-0905.m4a"   <!-- Chinese: the sample recording filename -->
        │
        ▼
~/.kimi-code/skills/meeting-asr/SKILL.md   ← trigger words, parameter conventions, script invocation (installed copy; source lives in the repo at skill/meeting-asr/)
        │
        ▼
skill/meeting-asr/scripts/meeting_asr.py   ← the single implementation (also usable standalone)
  ├─ 1. Preprocess: ffmpeg to 16kHz mono, low-bitrate mp3/opus (~32kbps)
  │      Rationale: raw 16k wav 5 min ≈ 9.6MB, ≈ 12.8MB after Base64, close to the 10MB limit;
  │      compressed 5 min ≈ 1.2MB, ≈ 1.6MB after Base64, ample headroom
  ├─ 2. Slice: 240s per segment (prefer VAD silence points, otherwise hard-cut); offsets go into the index
  ├─ 3. Cache: sha1(file)+slice_idx+offset+dur+ctx_hash → cache/*.json; skip on hit
  ├─ 4. Concurrent calls (default 3 concurrent, reduce on 429):
  │      messages = [hotwords context user message] + [assistant reply] + [input_audio(Base64)]
  │      Base64 over limit → automatically halve and retry (M1 test showed oss mode B unavailable, abandoned)
  ├─ 5. Merge: concatenate ordered by offset, paragraph markers [mm:ss] (slice granularity); failed slices get a placeholder line
  └─ 6. Output: <same-name>.transcript.md + .transcript.json + usage report
```

Directory conventions (repo `kimi-code-cli-asr` root; once the skill moved into the repo it became the single source):

```
kimi-code-cli-asr/
├── README.md / README_CN.md      ← repo entry (English default + Chinese)
├── AGENTS.md                     ← bilingual repo guidance for AI agents
├── asr-integration-prd-spec.md / _EN.md   ← this document (Chinese + English)
├── .gitignore                    ← recordings/cache/transcripts/credentials not committed
├── scripts/
│   └── m1_spike.py               ← M1 debug script (not a deliverable)
├── skill/meeting-asr/            ← single source of the skill
│   ├── SKILL.md                  ← trigger words, parameter conventions, invocation template
│   ├── scripts/meeting_asr.py    ← the implementation (also usable standalone)
│   ├── scripts/requirements.txt
│   ├── assets/hotwords.txt       ← brand names/media platform names/client names, one per line
│   ├── assets/context_prompt.txt ← domain background paragraph (for context enhancement, optional)
│   ├── references/api-notes.md   ← endpoint contract and troubleshooting notes
│   ├── evals/evals.json          ← skill evaluation set
│   └── cache/                    ← slice result cache (.gitignore)
└── testdata/                     ← test recordings (.gitignore)
```

### 2.4 Interfaces and Data Structures

**CLI interface**

```
PYTHONUTF8=1 python skill/meeting-asr/scripts/meeting_asr.py <audio_path>
    [--lang zh] [--slice-sec 240] [--concurrency 3]
    [--hotwords <hotwords file>]      # default skill/meeting-asr/assets/hotwords.txt
    [--context <domain context>]      # default skill/meeting-asr/assets/context_prompt.txt
    [--upload base64|oss]      # default base64; oss verified unavailable (interface kept)
    [--consent]                # first use must explicitly confirm cloud upload
```

**Request body template (once per slice, Base64 mode)**

```json
{
  "model": "qwen-audio-3.0-asr-flash",
  "input": {
    "messages": [
      {"role": "user", "content": [{"type": "input_text", "text": "<hotwords/domain context>"}]},
      {"role": "assistant", "content": [{"type": "text", "text": "好的，我会在识别中参考这些词汇。"}]},
      {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "data:audio/mpeg;base64,..."}}]}
    ]
  },
  "parameters": {"format": "mp3", "sample_rate": "16000"}
}
```

> Official convention for context enhancement: the context must appear **in pairs** of "user hotwords text + assistant reply" before the audio message; the model is highly tolerant of irrelevant content.
> (Note: the assistant reply above is intentionally kept in Chinese — it reads "OK, I will take these terms into account during recognition" — because the model is Chinese-first and this exact string was validated in testing.)

**Transcription result JSON schema (merged, saved to disk)**

```json
{
  "file": "客户周会-0905.m4a",
  "duration_sec": 3612.5,
  "model": "qwen-audio-3.0-asr-flash",
  "created_at": "2026-09-05T14:00:00+08:00",
  "slices": [
    { "idx": 0, "offset_sec": 0.0, "status": "ok", "text": "我们先过一下八月的投放数据……" }
  ],
  "usage": { "requests": 16, "billed_sec": 3612.5, "audio_tokens": null }
}
```

(The Chinese values above are sample data: the filename is the US-1 example recording, and the text means "Let's first go over August's campaign data…".)

**Markdown output format** (paragraph-level timestamps = slice offsets)

```
[00:00] 我们先过一下八月的投放数据……
[04:00] 接下来看小红书这边的投放节奏……
```

(Sample lines: "Let's first go over August's campaign data…" / "Next, let's look at the Xiaohongshu placement cadence…".)

### 2.5 Error Handling and Retry

| Scenario | Strategy |
|---|---|
| Network interruption/timeout | Slice-level exponential backoff retry, 3 attempts (2s/8s/32s) |
| 401/403 | Terminate immediately, prompt to check the `sk-sp-` Key and plan balance; no retry |
| 429 rate limit | Reduce concurrency to 1, honor Retry-After |
| 5xx | Exponential backoff retry; on persistent failure keep slice state and fill in on the next rerun |
| Single slice >5min or >10MB after Base64 encoding | Automatically shorten that slice (halve it) and retry |
| Base64 rejected by the endpoint | ~~Automatically fall back to input mode B~~ M1 test showed mode B is unavailable (getPolicy 404) → changed to: report the error and prompt to shorten slices/check plan permissions; no automatic fallback |
| oss:// URL call failure | Check the `X-DashScope-OssResourceResolve: enable` header and that the model parameter matches the one used at upload time |
| Response has no `output.text` | Print the raw response and mark the slice as failed (response structure per M1 test results) |
| Unsupported audio format | Unified transcoding via ffmpeg; if ffmpeg is missing, report the error with installation instructions |

### 2.6 Encoding and Environment Conventions (aligned with the global AGENTS.md)

- All `open()` calls explicitly specify `encoding='utf-8'`; subprocess calls add `encoding='utf-8', errors='replace'`
- The script entry self-checks `sys.stdout.encoding`; if not UTF-8, prompt to run with `PYTHONUTF8=1`
- Logs output only ASCII + Chinese, no emoji; currency uses `CNY` or `元` (yuan), not `¥`
- Complex commands are written into script files for execution; no heredoc/one-liner `python -c`
- Base64 strings never go into logs (log only the length), preventing log explosion and audio leakage

### 2.7 Milestones (for implementation reference)

| Milestone | Content | Exit criteria |
|---|---|---|
| **M1 (spike)** | Using a real `sk-sp-` Key, send a synchronous request with ≤30s of audio to the token-plan endpoint; confirm: ① the model is callable ② Base64 Data URL is accepted ③ response field structure ④ whether usage (audio_tokens/seconds) is returned ⑤ whether `uploads?action=getPolicy` works on the token-plan endpoint | curl/minimal script produces text; the five confirmations recorded in the document |
| M2 | Complete script: ffmpeg preprocessing, slicing, cache, concurrency, retry, Base64→oss fallback, merge, usage report | Acceptance criteria 1, 2, 3, 4 pass |
| M3 | Skill packaging + hotwords/context enhancement + meeting-minutes handoff template | US-1~3, US-5 verified end to end |
| M4 (optional) | LLM-inferred speakers, batch directory, srt export | Could items |

### 2.8 Open Questions

1. ~~**M1 spike five confirmations**~~ **Tested on 2026-09-04** (30 seconds of Chinese meeting audio, token-plan endpoint):
   - ① Model is callable: HTTP 200 + correct Chinese transcription ✅
   - ② Base64 Data URL (`data:audio/mpeg;base64,...`) accepted ✅ (30s/16kHz/32kbps mp3 ≈ 81KB, Data URL ≈ 108KB)
   - ③ Response structure: top-level `text`/`sentence`/`request_id` coexist redundantly with `output.*`; no choices; `output.sentence.words[]` carries word-level timestamps (milliseconds); paragraph-level `[mm:ss]` markers can be implemented with word-level timestamps (better than slice granularity)
   - ④ usage returns `{"duration": 20}` (seconds; no audio_tokens/seconds fields); the usage report is based on this
   - ⑤ getPolicy is **404 unavailable** on the token-plan endpoint; the same Key on the dashscope domain returns 401 InvalidApiKey → the oss fallback channel is not viable for this plan; the input mode is fixed to Base64
2. Whether the plan can be upgraded or topped up with `qwen-audio-3.0-asr-flash-filetrans` (12h long audio + speaker diarization) — if so, the architecture stays unchanged and only switches to the async transcription API; US-4 is automatically restored
3. The conversion rate between credits and transcription duration/tokens (affects amount estimation in the usage report); use the platform's real-time pricing; M1 test showed usage is metered by `duration` (seconds)
4. Division of labor with Feishu Minutes (the user already has the lark-minutes skill): recordings inside Minutes go through Minutes; local files go through this solution — to be written into the SKILL.md trigger-word notes

---

### 2.9 Tech Stack Review Conclusion (2026-09-04)

Conclusion: **keep the current tech stack** — no better alternative. Item-by-item review:

| Stack component | Current choice | Review conclusion |
|---|---|---|
| Language/runtime | Python 3.10+ | Keep. The environment is ready, and the global encoding conventions (PYTHONUTF8 etc.) revolve around Python; switching to Node/Go yields zero benefit |
| HTTP layer | `requests`, raw REST calls | Keep. All five M1 unknowns concern raw endpoint behavior; raw HTTP can fully print raw responses and precisely control `X-DashScope-*` headers — the most transparent for debugging; the OpenAI SDK is unusable (verified in 2.1); `httpx`/asyncio is unnecessary for 3-way concurrency |
| Audio preprocessing | ffmpeg CLI (subprocess) | Keep. pydub / ffmpeg-python are both ffmpeg wrappers (an extra dependency layer with no benefit); soundfile does not support m4a; ffmpeg is the standard solution on Windows |
| Concurrency model | ThreadPoolExecutor (3 concurrent) | Keep. I/O-bound with a concurrency of only 3; a thread pool suffices and async would only add complexity |
| Integration form | Skill + standalone script | Keep. See the three-option analysis in 2.2; for a single-Agent scenario, MCP adds an extra layer of protocol and process lifecycle |
| Model/endpoint | `qwen-audio-3.0-asr-flash` @ token-plan | No room for choice. The plan's only ASR model; local Whisper/FunASR conflicts with the "zero out-of-plan cost" requirement (1.4 Won't); filetrans is outside the plan |
| Cache/state | Slice-level JSON files | Keep. At this slice volume SQLite would be over-engineering; JSON allows direct visual inspection of resume state |

For the record (not adopted): the official `dashscope` Python SDK was verified to support custom endpoints via `dashscope.base_http_api_url` and custom headers ([Alibaba Cloud docs](https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope)); pointing it at the token-plan endpoint is theoretically feasible, but it would introduce an unverified third-party dependency variable into M1 and hide raw response details, so it is not used in the spike phase. If the requests route is blocked in M1, this route can be re-evaluated as a fallback.

Supporting minor improvements (implemented): step 0 of M2 creates `requirements.txt` (`requests>=2.31`) to pin dependencies; retry logic is hand-written (3 exponential-backoff retries, ~10 lines) instead of introducing tenacity.

---

## Part 3 · Implementation Log (2026-09-04 ~ 2026-09-05)

> The milestone conclusions and quality records of the original `asr-implementation-plan.md` have been merged into this section; that file has been retired.

### 3.1 Milestone Completion

| Milestone | Content | Result |
|---|---|---|
| 0.x preparation | Key configuration (setx, user level), ffmpeg 9.0.1 (winget), Python 3.13 + requests 2.34, directory skeleton | ✅ |
| M1 spike | Tested the five assumptions against the token-plan endpoint (`scripts/m1_spike.py`) | ✅ Conclusions in 2.8-1: endpoint works, Base64 accepted, usage is `duration` in seconds, word-level timestamps included, oss fallback channel unavailable (double-confirmed by 404/401) |
| M2 script | `skill/meeting-asr/scripts/meeting_asr.py`: preprocessing/slicing/cache/concurrency/retry/merge/usage report | ✅ A 21-minute real recording completed in 41 seconds (6 slices, 3 concurrent) |
| M3 skill | `skill/meeting-asr/` (single source in the repo; SKILL.md + scripts + assets + references, self-contained and distributable) | ✅ US-1/US-2/US-3/US-5 verified in real tests |
| M4 enhancements | Speaker inference labeling, batch directory, srt export, VAD slicing | Not started, on demand |

### 3.2 Quality Verification Records

- **M2 independent verification** (a subagent not involved in writing the code, falsification-oriented): first round "conditionally passed", finding 5 defects (1 high-risk: the cache key does not include slice-sec, causing timeline disorder after changing granularity; also corrupted-cache crash, 401 not terminating, etc.); after fixes, re-verification **passed** (0 real API calls in re-verification)
- **Skill evaluation** (skill-creator process): 3 test prompts × old/new versions run in parallel batches, all 14 assertions passed; 2 real issues found and fixed during the batch runs (residual `<|im_end|>` tokens mixed into the transcript → cleaned in the merge stage; duplicate billing from concurrent processes → `.lock` process lock)
- **Code review** (independent reviewer): all 13 findings fixed and re-verified as "releasable" — including 2 medium-risk (half-transcoded artifacts poisoning the cache → tmp+replace; cache anchored to CWD causing full re-billing after changing directories → anchored to the script's parent directory) + hotwords hash added to the cache key, stale lock PID liveness takeover, automatic halving when Base64 exceeds the limit, etc.

### 3.3 Operations Notes

- **Single source**: `skill/meeting-asr/` is the only source of the skill (scripts/docs/templates/eval set are all under version control). To reinstall locally after changes: copy the entire `skill/meeting-asr/` over `~/.kimi-code/skills/meeting-asr/` (preserve its `cache/`); for distribution, package with skill-creator's `package_skill.py` (`meeting-asr.skill` / `.zip`)
- **Cache is money**: cache hit condition = idx + offset + dur + context hash; modifying hotwords/background invalidates the entire cache for that file and re-bills it — think twice before deleting `cache/<sha1>/`
- **ffmpeg PATH**: after winget installation it is written to the user PATH, but terminals/processes opened before the installation need PATH set manually or to be restarted
