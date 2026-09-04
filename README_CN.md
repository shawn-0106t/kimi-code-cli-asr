# kimi-code-cli-asr

[English README](README.md)

在 Kimi Code CLI 里把本地会议录音转成文字：基于千问AI平台 Token Plan 套餐内的 ASR 模型（`qwen-audio-3.0-asr-flash`），一句话触发全流程——自动切片 → 并发转写 → 时间轴合并 → 同会话继续出会议纪要。全程只走套餐内模型，不产生套餐外费用。

## 功能特性

- **长音频 pipeline**：30~120 分钟录音自动切片（默认 240s/段，低于模型 5 分钟上限），3 路并发转写，按时间轴合并
- **断点续跑不重复计费**：切片级缓存（音频 SHA1 + 切片序号 + 上下文哈希），中断后重跑只补失败切片
- **热词上下文增强**：`skill/meeting-asr/assets/hotwords.txt` 中的品牌名/平台名/客户名作为对话上下文注入，提升专名识别率
- **成本透明**：每次运行打印音频时长、新请求数、缓存命中数、计费时长（`usage.duration` 合计）
- **隐私安全**：Base64 直传 token-plan 端点，不经第三方托管；API Key 从环境变量或 Windows 用户级注册表读取、不落盘；首次运行需 `--consent` 显式确认
- **Skill 形态**：自包含可分发的 skill 包（`meeting-asr.skill` / `.zip`），自然语言触发

## 快速开始

前提：Python 3.10+、ffmpeg（Windows 用 `winget install Gyan.FFmpeg`）、Token Plan Key（`sk-sp-` 前缀）。

```bash
pip install -r requirements.txt
setx QWEN_TOKEN_PLAN_KEY "sk-sp-..."   # Windows 持久化；或在 shell 中 export

# 转写（首次运行需确认上云处理）
PYTHONUTF8=1 python skill/meeting-asr/scripts/meeting_asr.py "path/to/meeting.m4a" --consent
```

产物落在音频同目录：`<同名>.transcript.md`（带 `[mm:ss]` 段落标记）与 `<同名>.transcript.json`（结构化切片 + 用量）。同一命令重跑零成本走缓存。

中断了直接重跑——已完成切片从缓存恢复。每个文件有进程锁，防止并发重复计费。

## Skill 形态（Kimi Code 用户推荐）

skill 就在本仓库 `skill/meeting-asr/`（自包含：`SKILL.md` + `scripts/` + `assets/` + `references/`）。安装 = 拷贝到用户级 skills 目录：

```bash
cp -r skill/meeting-asr ~/.kimi-code/skills/
```

装好后直接说"把这段会议录音转成文字"即可。飞书妙记内的录音不在本 skill 范围（请用 lark-meeting skill）。

## 文档

| 文件 | 内容 |
|---|---|
| [`asr-integration-prd-spec.md`](asr-integration-prd-spec.md) / [English](asr-integration-prd-spec_EN.md) | PRD + SPEC + 实施记录（里程碑、验证记录） |
| [`AGENTS.md`](AGENTS.md) | 给 AI agent 的中英双语仓库指引 |
| [`README.md`](README.md) | English README |

## 不入库的内容

`.gitignore` 默认排除原始录音（`testdata/`、`*.wav/mp3/m4a` 等）、切片缓存（`cache/`）、转写稿（`*.transcript.*`）与凭据文件。

## License

[MIT](LICENSE)
