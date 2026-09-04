# meeting-asr API 排障笔记（references/api-notes.md）

> 排障时才需要读本文件。正常情况下 SKILL.md 的流程足够。
> 内容来自 2026-09-04 M1 spike 实测与 code review，权威设计文档见开发仓库的 asr-integration-prd-spec.md。

## 端点与鉴权

- 端点：`POST https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
- 模型：`qwen-audio-3.0-asr-flash`（千问 Token Plan 套餐内唯一 ASR 模型）
- Header：`Authorization: Bearer $QWEN_TOKEN_PLAN_KEY`、`Content-Type: application/json`、`X-DashScope-SSE: disable`
- Key 为 `sk-sp-` 前缀，只适用于 token-plan 端点；在 `dashscope.aliyuncs.com` 上会 401 InvalidApiKey

## 输入方式

- 仅 Base64 Data URL：`data:audio/mpeg;base64,...` 放入 `input_audio.data`
- oss 临时 URL 兜底通道（`GET /api/v1/uploads?action=getPolicy`）在 token-plan 端点 404，不可用，不要再尝试
- 脚本预处理统一转 16kHz 单声道 32kbps mp3，单切片 ≤300s 时 Base64 约 1.6MB，远低于 10MB 上限

## 响应结构（M1 实测）

- 文本：`output.text`（顶层 `text` 冗余并存；兜底 `output.sentence.text`）；无 `choices` 字段
- usage：`{"duration": <秒>}`（按秒计，无 audio_tokens/seconds 字段）；失败请求无 usage 返回
- 附词级时间戳 `output.sentence.words[]`（begin_time/end_time 毫秒），`speaker_id` 恒为 null（模型不支持说话人分离）
- 偶发残留特殊 token（如 `<|im_end|>`），脚本合并阶段已过滤

## 错误矩阵与脚本行为

| 现象 | 含义 | 脚本行为 |
|---|---|---|
| 401/403 | Key 错误/失效或套餐余量不足 | 立即终止不重试；检查 Key 与套餐 |
| 429 | 限流 | 降并发为 1，按 Retry-After 等待（封顶 120s），指数退避 |
| 5xx / 网络异常 | 服务端或网络抖动 | 2s/8s/32s 指数退避重试 3 次，失败保留切片状态 |
| 其他 4xx | 请求被拒（如音频过长/格式问题） | 切片记 failed 并在 md 占位，重跑只补失败段 |
| getPolicy 404 | 正常，oss 通道不存在 | 不影响（默认 base64） |

## 缓存与计费

- 缓存位置：`脚本上一级/cache/<sha1(音频文件)>/slice_<idx>.json`，命中条件 = idx + offset + dur + 上下文哈希（ctx_hash）四者一致
- 修改热词表/领域背景会使全部缓存失效并重新计费——这是设计行为，改前想清楚
- `.lock` 进程锁防并发重复计费；进程强杀留下的 stale lock 会按 PID 探活自动接管
- 想强制重转某文件：删除对应 `cache/<sha1>/` 目录

## 依赖

- Python 3.10+，`pip install -r scripts/requirements.txt`（requests>=2.31）
- ffmpeg + ffprobe（Windows: `winget install Gyan.FFmpeg`；macOS: `brew install ffmpeg`）
