---
name: meeting-asr
description: 本地会议录音转文字稿 + 纪要衔接（千问 Token Plan / qwen-audio-3.0-asr-flash，不产生套餐外费用）。当用户要把本地音频文件（mp3/m4a/wav/flac 等）转成文字时使用——无论用户说的是"会议录音转文字""语音转写""出逐字稿""听打""transcribe""ASR"，还是只说"把这段录音整理成文字"，都应使用本 skill，即使用户没有明说"转写"二字。转写完成后用户要求基于转写稿出会议纪要时，也在本 skill 范围内（直接读转写稿，不重复调用 ASR）。分工边界：飞书妙记内的会议录音走 lark-meeting 相关 skill，本 skill 只处理本地音频文件。
---

# meeting-asr：本地会议录音转文字

把本地音频文件转写为带时间标记的文字稿，全程走千问 Token Plan 套餐内的 `qwen-audio-3.0-asr-flash`，不产生套餐外费用。本 skill 自包含：实现脚本在 `scripts/meeting_asr.py`（与本 SKILL.md 同 skill 目录），无需外部仓库。源头仓库：github.com/shawn-0106t/kimi-code-cli-asr（skill 目录的副本即本 skill，问题反馈与更新走该仓库）。

## 分工边界（重要）

- **本地音频文件**（用户在目录里给的 mp3/m4a/wav/flac）→ 用本 skill
- **飞书妙记里的会议录音** → 走 lark-meeting skill，不要用本 skill
- 模型不支持说话人分离；如用户要求区分说话人，转写完成后由你按语义推测分段标注，并明确标注"推测"

## 首次配置（用户的机器只需一次）

1. Python 3.10+，安装依赖：`pip install -r "${KIMI_SKILL_DIR}/scripts/requirements.txt"`
2. ffmpeg + ffprobe：Windows `winget install Gyan.FFmpeg`（装完重开终端）；macOS `brew install ffmpeg`
3. API Key（千问 Token Plan，`sk-sp-` 前缀）：
   - 通用：设环境变量 `QWEN_TOKEN_PLAN_KEY`
   - Windows 持久化：`setx QWEN_TOKEN_PLAN_KEY "sk-sp-..."`（脚本会从用户级注册表读取）
4. Key 严禁打印、落盘或写入任何文件/仓库

## 执行步骤

1. **参数提取**：从用户话语中提取音频文件路径（必须）。可选：切片秒数（默认 240，不用主动提）、热词表（见下）。
2. **运行**（必须带 PYTHONUTF8=1；>10 分钟的录音用后台任务运行）：

   ```bash
   PYTHONUTF8=1 python "${KIMI_SKILL_DIR}/scripts/meeting_asr.py" "<音频路径>"
   ```

   首次运行会要求确认上云：附加 `--consent`（音频经互联网发送至阿里云 token-plan 端点，Base64 直传不经第三方托管；确认一次即可，记录在 skill 目录的 cache/.consent_ok）。
3. **中断恢复**：Ctrl+C 或断网后重跑同一命令即可，已完成切片走缓存不重复计费。

## 热词与上下文增强

- 默认模板：`assets/hotwords.txt`（一行一个热词）、`assets/context_prompt.txt`（领域背景），脚本默认自动加载
- 用户有自己的热词表时用 `--hotwords <路径>` 指定
- 注意：修改热词/背景内容会使该文件全部缓存失效并重新计费（缓存键含上下文哈希），改之前提示用户

## 结果汇报格式

转写完成后向用户汇报：

- 输出文件：`<同名>.transcript.md`（[mm:ss] 段落标记，失败切片有占位行）、`<同名>.transcript.json`（结构化切片数据）
- 用量：音频时长、本次新请求数、缓存命中数、计费时长（usage.duration 合计）
- 转写稿开头 2~3 行预览，让用户确认质量
- 若有失败切片：明确告知数量，说明重跑同一命令可补齐

## 纪要衔接（转写后的连续动作）

用户接着要求"出会议纪要"时，直接读取 `.transcript.md` 内容整理（背景/结论/行动项/负责人）。不要重新调用 ASR。

## 排障

转写出错、限流、缓存异常、想了解端点细节时，读 `references/api-notes.md`（端点契约、M1 实测响应结构、错误矩阵、缓存机制）。

## 注意事项

- 单请求上限 5 分钟，脚本自动切片合并，无需用户手动拆分
- 只访问 token-plan 端点；`--upload oss` 已实测不可用（getPolicy 404），不要使用
- 缓存与 consent 锚定在 skill 目录的 `cache/`（与运行目录无关），跨项目共享；强制重转某文件：删除 `cache/<sha1>/` 对应目录
- 转写产物与录音属隐私数据；在 git 仓库中使用时确认它们在 .gitignore 中
