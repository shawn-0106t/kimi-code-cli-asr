# kimi-code-cli-asr

把千问AI平台 Token Plan 内的 ASR 模型（`qwen-audio-3.0-asr-flash`）接入 Kimi Code CLI，用于会议录音转文字：一句话触发 → 本地录音自动切片转写 → 文字稿落盘 → 同会话继续出会议纪要。

## 状态

**设计阶段 —— 当前无可执行代码。**

- [x] 官方文档实测调研（4 页，含端点/模型限制确认）
- [x] PRD + SPEC 定稿（v0.4）
- [x] 执行计划编写
- [ ] M1 spike：token-plan 端点 5 项假设待实测（**动工第一步**）
- [ ] M2 转写脚本 / M3 skill 封装

## 文档

| 文件 | 作用 |
|---|---|
| [`asr-integration-prd-spec.md`](asr-integration-prd-spec.md) | PRD + SPEC 混合文档：需求、选型、架构、接口、验收标准 |
| [`asr-implementation-plan.md`](asr-implementation-plan.md) | 执行计划：里程碑 0.1~M4 的逐步操作与出口标准 |

阅读顺序：先 SPEC 后 PLAN。要接手实现，从 PLAN 的「0. 开工前准备」开始。

## 关键约束速查

这几条是实测官方文档得出的，直接决定了架构设计：

- 套餐内唯一 ASR 模型：`qwen-audio-3.0-asr-flash`，单请求音频上限 **5 分钟** → 长录音必须切片
- 只能走 DashScope 风格同步端点 `services/aigc/multimodal-generation/generation`；**OpenAI 兼容端点不支持此模型**
- 输入支持 Base64 Data URL（编码后 ≤10MB）与 URL；官方另有临时 OSS 上传通道作兜底
- **不支持说话人分离**（该能力属于套餐外的 filetrans 模型）
- 支持**上下文增强**：热词/领域文本以成对 messages 传入，可提升品牌名、平台名等专名识别率

## 不入库的内容

`.gitignore` 默认排除原始录音（`testdata/`、`*.wav/mp3/m4a`）、切片缓存（`cache/`）、转写稿（`*.transcript.*`）与凭据文件。想备份文字稿，删掉 `.gitignore` 中对应两行的注释即可。

API Key 通过环境变量 `QWEN_TOKEN_PLAN_KEY` 注入，不写入任何文件。

## License

[MIT](LICENSE)
