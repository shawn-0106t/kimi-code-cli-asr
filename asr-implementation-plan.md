# ASR 接入执行计划（Implementation Plan）

> 版本：v1.0  日期：2026-09
> 依据：`asr-integration-prd-spec.md` v0.4（同目录）
> 状态：待开工。本文档只含执行步骤，开工后按里程碑顺序推进，每步验证通过再进下一步
> 环境前提：Windows + Git Bash；Kimi Code CLI；千问 Token Plan Key（`sk-sp-` 前缀）

---

## 0. 开工前准备（一次性，约 10 分钟）

| # | 动作 | 验证 |
|---|---|---|
| 0.1 | 用户配置环境变量 `QWEN_TOKEN_PLAN_KEY=sk-sp-...`（Windows 用户级环境变量，或当前 shell `export`；**不写入任何文件/仓库**） | `echo ${QWEN_TOKEN_PLAN_KEY:0:6}` 输出 `sk-sp-` |
| 0.2 | 确认 ffmpeg 可用；缺失则安装（winget：`winget install Gyan.FFmpeg`） | `ffmpeg -version` 有输出 |
| 0.3 | 确认 Python 3.10+ 与 `requests` 可用 | `PYTHONUTF8=1 python -c "import requests; print(requests.__version__)"` |
| 0.4 | 准备一段 30 秒内的测试音频（中文语音，wav/mp3 均可），放入 `testdata/`（仓库根下，已被 .gitignore 排除） | 文件存在且可播放 |
| 0.5 | 创建目录骨架：`scripts/`、`config/`、`cache/`、`testdata/`；`.gitignore` 加入 `cache/`、`testdata/` | 目录就位 |

---

## M1 · Spike：验证 token-plan 端点五项假设（最关键，先做）

产出：`scripts/m1_spike.py`（最小验证脚本）+ 结论回填到 SPEC 2.8

**步骤：**

1. 写 `scripts/m1_spike.py`：
   - ffmpeg 将测试音频转为 16kHz 单声道 mp3（约 32kbps）
   - Base64 编码为 Data URL（`data:audio/mpeg;base64,...`）
   - POST `https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
   - header：`Authorization: Bearer $QWEN_TOKEN_PLAN_KEY`、`Content-Type: application/json`、`X-DashScope-SSE: disable`
   - body：`{"model":"qwen-audio-3.0-asr-flash","input":{"messages":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"<data-url>"}}]}]},"parameters":{"format":"mp3","sample_rate":"16000"}}`
   - 打印 HTTP 状态码与**完整原始响应**（Base64 不回显）
2. 逐项确认并记录：
   - [ ] ① 模型在 token-plan 端点可调通（200 + 有转写文本）
   - [ ] ② Base64 Data URL 输入被接受
   - [ ] ③ 响应字段结构（预期 `output.text`；记录实际结构，修正 SPEC 2.1 解析规则）
   - [ ] ④ 是否返回 usage（`audio_tokens`/`seconds`）
   - [ ] ⑤ `GET <token-plan-base>/api/v1/uploads?action=getPolicy&model=qwen-audio-3.0-asr-flash` 是否可用（oss 兜底通道）
3. 失败处理：
   - 模型名被拒 → 换不带版本/其他拼写重试一次，仍失败则找用户确认套餐内确切模型 ID
   - Base64 被拒 → 改测 oss 临时 URL 链路（⑤若为通则走方式 B）
   - 端点 404 → 用 SPEC 中的 dashscope 官方域名对比测试，定位是端点路径还是权限问题
4. 将五项结果回填 `asr-integration-prd-spec.md` 2.1/2.8，M1 才算完成

**出口标准**：测试音频转出正确中文文本；五项确认全部有结论。

---

## M2 · 完整转写脚本

产出：`scripts/meeting_asr.py`

**实现顺序（每步本地可测）：**

1. **预处理模块**：ffmpeg 转码（16k 单声道 mp3 32kbps）+ ffprobe 测时长；ffmpeg 缺失则报错并提示安装
2. **切片模块**：按 `--slice-sec`（默认 240）硬切；VAD 静默点优化列为增强项（M2 不阻塞，可 M4 再做）；切片偏移量写入索引
3. **缓存模块**：`cache/<sha1(file)>/<idx>.json`；命中跳过；切片状态机（pending/ok/failed）
4. **调用模块**：按 SPEC 2.4 请求体逐切片调用；并发 3；重试（2s/8s/32s）；429 降并发至 1；401/403 立即终止；Base64 被拒自动降级 oss 上传（M1 结论可用时）
5. **上下文增强模块**：读 `config/hotwords.txt` + `config/context_prompt.txt`，组装 user/assistant 成对上下文消息
6. **合并输出**：按偏移量拼接 → `<同名>.transcript.md`（`[mm:ss]` 段落标记）+ `.transcript.json`（SPEC 2.4 schema）+ 用量报告（时长/请求数/usage）
7. **合规自检**：`--consent` 首次确认；stdout 编码自检；日志无 emoji、无 Base64、无 `¥`

**出口标准（= SPEC 1.6 验收）：**

- [ ] 60 分钟中文 m4a 一次跑通，产出完整文字稿
- [ ] UTF-8 无乱码；GBK 控制台日志正常
- [ ] 中途 `Ctrl+C` 后重跑，已完成切片不重复请求（数 cache 命中日志验证）
- [ ] 全程只访问 token-plan 端点

**独立验证要求**（用户全局规范）：交付级脚本须由未参与编写的 subagent 独立测试——只给它需求与脚本路径，以证伪为导向（假设至少 2 处错误），不转述作者预期。

---

## M3 · Skill 封装

产出：`~/.kimi-code/skills/meeting-asr/SKILL.md`

1. YAML front matter：`name: meeting-asr`；description 含触发词（会议录音、转文字、语音转写、ASR、transcribe 等）
2. 正文约定：参数提取规则（文件路径、语言、是否加热词）、调用脚本的命令模板（带 `PYTHONUTF8=1`）、结果汇报格式
3. 写入分工边界：妙记内录音走 lark-minutes，本地文件走本 skill（SPEC 2.8-4）
4. 在 Kimi Code 中实测 US-1~US-3、US-5：一句话触发 → 转写 → 同会话出纪要

**出口标准**：自然语言触发成功率与转写质量可接受；纪要与转写稿内容一致。

---

## M4 · 可选增强（按需启动，不默认做）

- [ ] LLM 推测说话人标注（US-4，输出标注"推测"）
- [ ] 批量目录处理
- [ ] srt/vtt 导出
- [ ] VAD 静默点智能切片
- [ ] 若套餐升级 filetrans：切换异步转写接口，恢复说话人分离（SPEC 2.8-2）

---

## 风险与依赖速查

| 风险 | 缓解 |
|---|---|
| M1 五项任一不通 | 见 M1 步骤 3 的失败处理；全部不通则暂停并找用户确认套餐能力 |
| ffmpeg 不在用户机器 | 0.2 步先装；脚本内做存在性检查 |
| 长音频总时长超预期（>2h） | 切片天然支持；注意总请求数与限流 |
| 隐私：oss 兜底通道 48h 留存 | 默认 Base64；降级时明确提示 |
| 临时文件 | 每步用后即删（WebBridge 请求文件、调试脚本均如此），交付脚本保留在 `scripts/` |

## 会话衔接说明（给下一个执行会话）

1. 先读仓库根的 `asr-integration-prd-spec.md`（设计依据）+ `asr-implementation-plan.md`（本文档）
2. 从 0.1 开始逐步执行；M1 未完成前不要写 M2 代码
3. 每里程碑结束更新本文档对应 checkbox 与 SPEC 开放问题
