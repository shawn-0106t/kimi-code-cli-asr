# AGENTS.md

> 本文件指导 AI agent 在本仓库工作。中文在上，English below。
> This file guides AI agents working in this repository.

---

## 中文

### 项目是什么

把千问AI平台 Token Plan 的 ASR 模型（`qwen-audio-3.0-asr-flash`）接入 Kimi Code CLI：本地会议录音 → 自动切片转写 → 文字稿落盘。设计/验收/实施记录见 `asr-integration-prd-spec.md`（英文版 `_EN.md`）。

### 仓库结构

```
skill/meeting-asr/       skill 唯一源头（SKILL.md + scripts/meeting_asr.py + assets/ + references/ + evals/）
scripts/m1_spike.py      M1 调试脚本（非交付物，不要当作质量标准）
requirements.txt         requests>=2.31（与 skill/meeting-asr/scripts/requirements.txt 相同）
testdata/                录音（.gitignore，永不提交）；切片缓存位于 skill/meeting-asr/cache/
```

热词表在 `skill/meeting-asr/assets/hotwords.txt`（脚本默认读取，改它会使缓存失效重计费）。

### 红线（违反 = 真实损失）

- **缓存即金钱**：API 按音频时长计费。缓存命中条件 = idx + offset + dur + ctx_hash（热词/背景哈希）。删除 `cache/<sha1>/`、改热词、改 `--slice-sec` 都会触发重新计费，操作前必须告知用户
- **Key 安全**：`QWEN_TOKEN_PLAN_KEY`（`sk-sp-` 前缀）从环境变量或 Windows 用户级注册表读取；严禁打印、落盘、写入任何文件或日志
- **只访问 token-plan 端点**；`--upload oss` 通道实测不可用（404），不要"修复"它
- **并发锁**：`cache/<sha1>/.lock` 防重复计费，不要绕过

### 编码与环境（Windows + Git Bash）

- 运行脚本一律 `PYTHONUTF8=1 python ...`；文件读写显式 UTF-8；日志无 emoji、无 `¥`
- ffmpeg/ffprobe 必须可用（`winget install Gyan.FFmpeg`，装后重开终端）

### 修改纪律

- **单一源头**：`skill/meeting-asr/` 是唯一源头。修改后如需在本机使用：拷贝整个 `skill/meeting-asr/` 覆盖 `~/.kimi-code/skills/meeting-asr/`（注意保留目标下的 `cache/`）；需要分发时再用 skill-creator 的 `package_skill.py` 打包
- **交付验证**：交付级改动须由未参与编写的 subagent 做证伪导向独立测试；功能修复跑回归（现有缓存应全命中、0 新请求）
- 文档变更同步中英两份（`*_EN.md` / `README.md` / `README_CN.md`）

---

## English

### What this is

Integrates the Qwen AI platform Token Plan ASR model (`qwen-audio-3.0-asr-flash`) into Kimi Code CLI: local meeting recordings → auto-sliced transcription → transcript files. See `asr-integration-prd-spec_EN.md` for design, acceptance, and implementation records.

### Repository layout

```
skill/meeting-asr/       Single source of truth for the skill (SKILL.md + scripts/meeting_asr.py + assets/ + references/ + evals/)
scripts/m1_spike.py      M1 debug script (not a deliverable; not a quality reference)
requirements.txt         requests>=2.31 (same as skill/meeting-asr/scripts/requirements.txt)
testdata/                Audio files (gitignored, never commit); slice cache lives in skill/meeting-asr/cache/
```

Hotwords live in `skill/meeting-asr/assets/hotwords.txt` (read by default; editing it invalidates cache and re-bills).

### Red lines (violations = real monetary loss)

- **Cache is money**: the API bills by audio duration. Cache hits require idx + offset + dur + ctx_hash (hotword/context hash). Deleting `cache/<sha1>/`, editing hotwords, or changing `--slice-sec` triggers re-billing — always warn the user first
- **Key safety**: `QWEN_TOKEN_PLAN_KEY` (`sk-sp-` prefix) comes from env var or Windows user registry; never print, persist, or commit it
- **Token-plan endpoint only**; the `--upload oss` channel is confirmed unavailable (404) — do not "fix" it
- **Process lock**: `cache/<sha1>/.lock` prevents double billing; do not bypass

### Encoding & environment (Windows + Git Bash)

- Always run scripts as `PYTHONUTF8=1 python ...`; explicit UTF-8 file I/O; no emoji, no `¥` in logs
- ffmpeg/ffprobe required (`winget install Gyan.FFmpeg`, then restart the terminal)

### Change discipline

- **Single source**: `skill/meeting-asr/` is the only source of truth. After changes, reinstall by copying the whole `skill/meeting-asr/` over `~/.kimi-code/skills/meeting-asr/` (preserve its `cache/`); repackage with skill-creator's `package_skill.py` when distributing
- **Delivery verification**: delivery-grade changes must be independently tested by a subagent that did not write the code, with a falsification mindset; for fixes, run regression (existing cache should fully hit, 0 new requests)
- Keep bilingual docs in sync (`*_EN.md` / `README.md` / `README_CN.md`)
