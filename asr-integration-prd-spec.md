# Kimi Code CLI 接入千问AI平台 Token Plan（ASR）—— PRD / SPEC

> 版本：v0.4  日期：2026-09
> 变更记录：
> - v0.2：确认 token plan 为千问AI平台 Token Plan（DashScope 兼容，`sk-sp-` Key），选型 filetrans
> - v0.3：WebBridge 实探 2 页，确认套餐仅含 `qwen-audio-3.0-asr-flash`（同步、≤5 分钟），重写选型与架构
> - v0.4：WebBridge 增探 2 页（OpenAI 兼容接口、临时 URL 上传），明确接口边界、新增上传兜底方案、修订切片码率策略
> 定位：PRD（为什么做、做什么）+ SPEC（怎么做）混合文档，供评审后直接进入实现
> 范围声明：本文档只到设计为止，不含实现代码

---

## Part 0 · 调研结论（含 WebBridge 实探结果）

### 0.1 GitHub / 技术社区

不存在"Kimi Code CLI + ASR token plan"的现成方案，只有相邻组件（Kimi-Audio 开源模型、whisper-mcp、ffvoice-engine 等）。Kimi API 平台无公开音频转写端点。结论：需自行设计。

### 0.2 官方文档实探（2026-09，经 Kimi WebBridge 在用户真实浏览器中读取，共 4 页）

**页面 1：[Token Plan 接入多模态生成模型](https://platform.qianwenai.com/docs/token-plan/best-practices/multimodal-generation)**

- 只含文生图、文生视频、TTS 三个接入示例，**无 ASR 示例**
- 确认接入范式：Skill / Slash Command 形态 + `sk-sp-` Key 走环境变量
- token-plan 端点：`https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

**页面 2：[语音识别 · 录音文件转写](https://platform.qianwenai.com/docs/developer-guides/speech/asr)**

| 事实 | 影响 |
|---|---|
| `qwen-audio-3.0-asr-flash`（套餐唯一 ASR 模型）走**同步调用**，端点同为 `services/aigc/multimodal-generation/generation` | 与 token-plan 文生图同一端点路径，大概率可通（M1 验证） |
| 单请求音频上限 **5 分钟** | 长会议必须自研切片 pipeline |
| 输入支持 **URL / Base64** | 本地直传，无需公网托管 |
| 响应结构特殊：`output.text`（及 `output.output.sentence.text`），**无 choices 字段** | 解析逻辑需专门适配 |
| **不支持说话人分离**（仅 filetrans 支持） | US-4 降级 |
| **支持上下文增强**：热词表/领域文本作为对话历史传入 messages（user+assistant 成对），显著提升专名识别 | 热词方案采用此机制 |
| 官方 FAQ：避免把音频切得过短 | 切片粒度 3~5 分钟，不小于 1 分钟 |

**页面 3：[录音文件识别（Qwen-ASR）OpenAI 兼容](https://platform.qianwenai.com/docs/api-reference/speech-recognition/qwen-asr/openai)**

| 事实 | 影响 |
|---|---|
| OpenAI 兼容端点（`/compatible-mode/v1/chat/completions`）**仅支持 `qwen3-asr-flash`**，不支持本套餐的 `qwen-audio-3.0-asr-flash` | 本方案**不能走** OpenAI 兼容端点，只能用 DashScope 风格 multimodal-generation 端点 |
| Base64 用 Data URL 格式（`data:audio/wav;base64,...`），该模型要求编码后 **≤10MB** | 切片必须压缩编码后传输（见 2.3） |
| 响应 `usage` 含 `audio_tokens`、`seconds` 字段 | 若本模型同样返回，用量报告可取真实值而非估算（M1 验证） |
| 专有参数 `asr_options`（language、enable_itn）经 extra_body 传入 | 仅适用 qwen3-asr-flash，本模型不用 |

**页面 4：[上传文件获取临时 URL](https://platform.qianwenai.com/docs/api-reference/more/upload-file-get-temporary-url)**

| 事实 | 影响 |
|---|---|
| 平台提供两步上传：`GET /api/v1/uploads?action=getPolicy&model=<模型>` 拿 OSS 凭证 → POST 到 upload_host → 得 `oss://` 临时 URL（**48 小时有效**） | Base64 走不通/文件过大时的**官方兜底上传通道**，无需自备 OSS |
| 用 oss:// URL 调模型时必须加 header `X-DashScope-OssResourceResolve: enable`；上传凭证的 model 参数必须与调用模型一致 | 写入 SPEC 调用约定 |
| 凭证接口限流 100 QPS（账号+模型维度） | 本场景（每文件一次）无影响 |
| 示例 base URL 均为 `dashscope.aliyuncs.com` | token-plan 端点是否同样支持，列入 M1 spike |

> M1 未验证项汇总：token-plan 端点对本模型同步调用的支持、Base64 输入、响应字段结构、usage 字段、uploads getPolicy 在 token-plan 端点的可用性。

---

## Part 1 · PRD

### 1.1 背景

用户（广告媒介策划）日常工作包含大量客户会、内部对齐会，会后需将录音（mp3/m4a/wav，中文为主、常中英混排、30~120 分钟）整理成文字稿与纪要。痛点：网页工具流程断在 CLI 之外；本地 Whisper 慢且中文专名差；希望在 Kimi Code CLI 内闭环，并复用已持有的千问 Token Plan（套餐内唯一 ASR 模型为 `qwen-audio-3.0-asr-flash`）。

### 1.2 目标

在 Kimi Code CLI 中，用一句话（或一条命令）把本地会议录音转成文字稿，全部走 Token Plan 套餐内的 `qwen-audio-3.0-asr-flash`，不产生套餐外费用。

### 1.3 用户故事

- US-1：把 `客户周会-0905.m4a` 放进工作目录，对 Kimi Code 说"把这段会议录音转成文字"，得到带段落级时间标记的文字稿落盘
- US-2：30~120 分钟长录音自动切片、逐段转写、按时间轴合并，无需手动拆分
- US-3：文字稿生成后，同一会话里直接要求"基于这份转写稿出一份会议纪要"
- US-4（降级）：模型不支持说话人分离；改为可选的 LLM 后处理——转写完成后由 Kimi 按语义推测分段说话人，标注为"推测"
- US-5：用热词/领域上下文（品牌名、媒体平台名、客户名）提升专名识别率（官方上下文增强机制）

### 1.4 功能范围（MoSCoW）

| 级别 | 功能 |
|---|---|
| Must | 单文件转写（mp3/m4a/wav/flac 等，ffmpeg 统一转码）；**自动切片 + 并发调用 + 时间轴合并**；Base64 Data URL 传输（失败时降级临时 URL 上传）；切片级缓存断点续跑；上下文增强（热词表）；结果落盘（md + JSON）；用量报告 |
| Should | 段落级时间标记（按切片偏移量推算）；纪要衔接 prompt 模板 |
| Could | LLM 推测说话人标注（US-4）；批量目录处理；srt 导出 |
| Won't（本期） | 说话人分离（模型不支持，除非套餐升级 filetrans）；实时流式转写；本地模型；GUI；录音采集 |

### 1.5 非功能需求

- **环境**：Windows 10/11 + Git Bash；Python 加 `PYTHONUTF8=1`；文件读写显式 UTF-8；终端输出无 emoji、无 GBK 字符集外符号
- **隐私**：默认 Base64 直传，不经第三方托管；降级临时 URL 方案时录音在官方 OSS 临时空间留存 48 小时（私有 ACL），首次使用 `--consent` 显式确认
- **成本透明**：每次打印转写时长、请求次数，及响应返回的 usage 字段（若端点返回）
- **可恢复**：切片级结果缓存（音频 SHA1 + 切片序号），中断重跑只补失败切片

### 1.6 验收标准

1. 60 分钟中文会议 m4a，一句话/一条命令触发，产出完整合并文字稿
2. 输出 UTF-8 无乱码，终端日志在 GBK 控制台无异常符号
3. 中途断网重跑，已成功切片不重复请求
4. 全程只调 token-plan 端点、只用 `qwen-audio-3.0-asr-flash`

---

## Part 2 · SPEC

### 2.1 模型与端点

| 项 | 值 |
|---|---|
| 模型 | `qwen-audio-3.0-asr-flash`（套餐内唯一 ASR 模型） |
| 端点 | `POST https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`（DashScope 风格同步接口；**非** OpenAI 兼容端点，该端点仅支持 qwen3-asr-flash） |
| 鉴权 | `Authorization: Bearer $QWEN_TOKEN_PLAN_KEY`（`sk-sp-` 前缀，环境变量注入，不落盘） |
| 调用方式 | 同步，逐切片调用；header 加 `X-DashScope-SSE: disable` |
| 输入方式 A（默认） | Base64 Data URL：`data:audio/mpeg;base64,...` 放入 `input_audio.data` |
| 输入方式 B（兜底） | 临时 URL 上传：`GET <base>/api/v1/uploads?action=getPolicy&model=qwen-audio-3.0-asr-flash` → POST 至 `upload_host` → 得 `oss://` URL（48h 有效）；调用时加 header `X-DashScope-OssResourceResolve: enable` |
| 参数 | `parameters.format`、 `parameters.sample_rate` |
| 响应解析 | 取 `output.text`（兜底 `output.output.sentence.text`）；**无 choices 字段**；usage 字段若存在则记录 |
| 限制 | 单请求 ≤5 分钟；多语种及方言；支持上下文增强；无说话人分离 |

### 2.2 集成形态（三选一的结论）

| 维度 | ① Skill + 脚本（推荐） | ② MCP server | ③ 独立 CLI 脚本 |
|---|---|---|---|
| 使用方式 | Kimi Code 里一句话触发；也可手工跑脚本 | `mcp.json` 注册为 Agent 工具 | 终端手敲命令 |
| 环境依赖 | Python 3.10+、`requests`、`ffmpeg`；skill 清单 | 同左 + MCP 框架 + 注册 | 仅 Python + requests + ffmpeg |
| 可维护性 | 高，且为官方 best practice 推荐形态 | 中，多一层协议与进程生命周期 | 高，但无自然语言入口 |
| 适用场景 | 日常高频、转写→纪要连续动作 | 多 Agent 编排（本期无此需求） | 偶发/批处理 |

**推荐 ① Skill + 脚本**（脚本本体即形态 ③，独立可用）。skill 路径：`~/.kimi-code/skills/meeting-asr/SKILL.md`，含 YAML front matter（name/description/触发词）。

### 2.3 架构与转写 pipeline

```
用户: "把 客户周会-0905.m4a 转成文字"
        │
        ▼
~/.kimi-code/skills/meeting-asr/SKILL.md   ← 触发词、参数约定、调脚本
        │
        ▼
scripts/meeting_asr.py                    ← 唯一实现体（也独立可用）
  ├─ 1. 预处理：ffmpeg 转 16kHz 单声道、低码率 mp3/opus（约 32kbps）
  │      理由：原始 16k wav 5 分钟 ≈ 9.6MB，Base64 后 ≈ 12.8MB 逼近 10MB 上限；
  │      压缩后 5 分钟 ≈ 1.2MB，Base64 后 ≈ 1.6MB，余量充足
  ├─ 2. 切片：按 240s/段（VAD 静默点优先，否则硬切），偏移量入索引
  ├─ 3. 缓存：sha1(file)+slice_idx → cache/*.json，命中跳过
  ├─ 4. 并发调用（默认 3 并发，429 降并发）：
  │      messages = [热词上下文 user 消息] + [assistant 应答] + [input_audio(Base64)]
  │      Base64 超限/被拒 → 自动降级临时 URL 上传（方式 B）
  ├─ 5. 合并：按偏移量排序拼接，段落标记 [mm:ss]（切片粒度）
  └─ 6. 输出：<同名>.transcript.md + .transcript.json + 用量报告
```

目录约定（当前项目 `asr/`）：

```
asr/
├── asr-integration-prd-spec.md   ← 本文档
├── scripts/
│   └── meeting_asr.py            ← 实现时创建
├── config/
│   ├── hotwords.txt              ← 品牌名/媒体平台名/客户名，一行一个
│   └── context_prompt.txt        ← 领域背景段落（上下文增强用，可选）
└── cache/                        ← 切片结果缓存（.gitignore）
```

### 2.4 接口与数据结构

**CLI 接口**

```
PYTHONUTF8=1 python scripts/meeting_asr.py <audio_path>
    [--lang zh] [--slice-sec 240] [--concurrency 3]
    [--hotwords config/hotwords.txt]
    [--context config/context_prompt.txt]
    [--upload base64|oss]      # 默认 base64，失败自动降级 oss
    [--consent]                # 首次使用必须显式确认上云
```

**请求体模板（每切片一次，Base64 方式）**

```json
{
  "model": "qwen-audio-3.0-asr-flash",
  "input": {
    "messages": [
      {"role": "user", "content": [{"type": "input_text", "text": "<热词表/领域上下文>"}]},
      {"role": "assistant", "content": [{"type": "text", "text": "好的，我会在识别中参考这些词汇。"}]},
      {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "data:audio/mpeg;base64,..."}}]}
    ]
  },
  "parameters": {"format": "mp3", "sample_rate": "16000"}
}
```

> 上下文增强的官方约定：上下文需以"user 热词文本 + assistant 应答"**成对**出现在音频消息之前；模型对无关内容容忍度高。

**转写结果 JSON schema（合并后落盘）**

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

**Markdown 输出格式**（段落级时间标记 = 切片偏移量）

```
[00:00] 我们先过一下八月的投放数据……
[04:00] 接下来看小红书这边的投放节奏……
```

### 2.5 错误处理与重试

| 场景 | 策略 |
|---|---|
| 网络中断/超时 | 切片级指数退避重试 3 次（2s/8s/32s） |
| 401/403 | 立即终止，提示检查 `sk-sp-` Key 与套餐余量，不重试 |
| 429 限流 | 并发降至 1，遵循 Retry-After |
| 5xx | 指数退避重试；连续失败保留切片状态，下次重跑补齐 |
| 单切片 >5min 或 Base64 编码后 >10MB | 自动缩短该切片（减半）后重试 |
| Base64 被端点拒绝 | 自动降级输入方式 B（临时 URL 上传），并提示 48h 留存 |
| oss:// URL 调用失败 | 检查 `X-DashScope-OssResourceResolve: enable` header 与上传时 model 参数一致性 |
| 响应无 `output.text` | 打印原始响应并标记切片失败（响应结构以 M1 实测为准） |
| 音频格式不支持 | ffmpeg 统一转码；ffmpeg 缺失则报错并给出安装提示 |

### 2.6 编码与环境规范（对齐全局 AGENTS.md）

- 所有 `open()` 显式 `encoding='utf-8'`；subprocess 加 `encoding='utf-8', errors='replace'`
- 脚本入口自检 `sys.stdout.encoding`，非 UTF-8 时提示以 `PYTHONUTF8=1` 运行
- 日志只输出 ASCII + 中文，不输出 emoji；金额单位用 `CNY` 或 `元`，不用 `¥`
- 复杂命令写入脚本文件执行，不用 heredoc/单行 `python -c`
- Base64 字符串不进日志（只打长度），防止日志爆炸与音频外泄

### 2.7 里程碑（供后续实现参考）

| 里程碑 | 内容 | 出口标准 |
|---|---|---|
| **M1（spike）** | 用真实 `sk-sp-` Key 对 token-plan 端点发一条 ≤30s 音频的同步请求，确认：①模型可调通 ②Base64 Data URL 被接受 ③响应字段结构 ④是否返回 usage（audio_tokens/seconds）⑤`uploads?action=getPolicy` 在 token-plan 端点是否可用 | curl/最小脚本转出文字，五项确认记录进文档 |
| M2 | 完整脚本：ffmpeg 预处理、切片、缓存、并发、重试、Base64→oss 降级、合并、用量报告 | 验收 1、2、3、4 通过 |
| M3 | skill 封装 + 热词/上下文增强 + 纪要衔接模板 | US-1~3、US-5 走通 |
| M4（可选） | LLM 推测说话人、批量目录、srt 导出 | Could 项 |

### 2.8 开放问题

1. **M1 spike 五项确认**（见 2.7），官方文档示例均基于 `dashscope.aliyuncs.com`，token-plan 端点行为需实测
2. 套餐是否可升级/加购 `qwen-audio-3.0-asr-flash-filetrans`（12h 长音频 + 说话人分离）——若可，架构不变、改走异步转写接口即可，US-4 自动恢复
3. Credits 与转写时长/token 的折算率（影响用量报告金额估算），以平台实时价为准
4. 与飞书妙记（用户已有 lark-minutes skill）的分工：妙记内录音走妙记，本地文件走本方案 —— 写入 SKILL.md 触发词说明
