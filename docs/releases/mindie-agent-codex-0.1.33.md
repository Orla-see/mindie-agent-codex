# mindie-agent-codex 0.1.33：发布元数据恢复与错误分类

- 将共享 knowledge 基线更新至正式 0.13.21，防止无效 HTTP 200 JSON 在 TTL/ETag 缓存中反复复用，并拒绝截断响应。
- 同时修复组件与激活子进程的错误来源传递，保留原始诊断引用。
- 同时包含此前主分支已通过 CI 的 Windows CLI UTF-8 `--input-file`、输入阶段诊断与服务 GUI 启动器验证修复（#118）；交互式 Windows 桌面端到端验收仍单独记录。
- 发布、候选环境准备和正在运行的宿主实际加载分别核验；不重启活跃会话，不修改用户授权或产品历史通道。

- Astra 审查补修：组件附件先验证大小、摘要和内容再缓存；非法 Content-Length 在 HTTP 边界分类，本地解析与完整性故障继续可报告。

关联诊断：[mindie-agent#354](https://github.com/mindie-agent/mindie-agent/issues/354)。历史响应正文不可用，本版本不推断当时的初始损坏原因。
