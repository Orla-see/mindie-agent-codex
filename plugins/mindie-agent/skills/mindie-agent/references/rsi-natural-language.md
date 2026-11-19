# 自然语言 RSI 执行说明

RSI 会把用户的自然语言优化需求转换为经过校验的
`mindie-agent/rsi-plan/v1` 计划。计划负责连接自然语言理解和确定性运行时。
计划及所需输入通过校验前，不会执行远端修改。

## 用户需要提供的信息

用户需要提供：

- 能复现基线服务的启动脚本或启动命令
- 性能测试脚本、命令、配置文件，或清晰的测试描述
- 优化目标，例如吞吐、首 token 延迟、端到端延迟、显存，或每次验证接受的 token 数

测试描述应尽量包含 batch size 或并发数、输入和输出长度、数据集或 prompt 选择以及采样配置。
模型和 tokenizer 版本可以省略，运行时会使用默认值。默认预热 1 次、实测重复 3 次，
只有完成至少 3 次有效实测的候选才允许晋级。
只有“吞吐 1000 tokens/s”这类数字属于已有运行结果，不能直接复现实验。
缺少必要信息时返回 `needs_input`，明确列出需要补充的字段。
系统不会自行编造命令、路径、测试凭据或通过门禁的结果。

标准化计划包含启动脚本、测试定义、基线、优化目标、执行步骤、设备分配、
运行时绑定和 payload。主机路径和连接信息只保留在计划或运行时绑定中，
不会写入知识卡片。

RSI 核心代码位于 `scripts/mindie_rsi/`，不依赖 Codex 或 Claude API。
`rsi_entry.py`、`rsi_runtime_adapter.py` 和宿主绑定文件只负责启动核心、传递 JSON
和执行宿主提供的 argv 命令。安装时核心包会复制到 RSI 根目录的 `bin/mindie_rsi/`，
其他宿主只需调用同一个 JSON entrypoint 即可复用计划、候选、门禁、记忆和结果沉淀逻辑。

## 默认执行流程

1. 通过只读操作检查源码目录、RSI 运行目录、启动脚本、测试定义、设备和可用绑定。
2. 校验计划，申请运行时租约，并复用返回的 execution ID。
3. 通过运行时适配器执行 prepare、build、profile、accuracy、performance、record 和 cleanup。
4. 要求每个动作返回 receipt。失败或未知状态会停止晋级，完成状态核对后才能重试。
5. 只有通过用户指定的正确性和性能门禁，才会晋级为 `active-best`。
   候选失败时保留基线，并保存已经通过门禁的最佳候选。

适配器使用 argv 数组执行命令，不经过 shell。
如果运行时绑定没有覆盖 `benchmark_cmd`，提供的 `test_command` 会作为 benchmark 命令使用。
自然语言 `test_description` 必须先解析成具体命令才能执行。

用户没有指定 profiler 时，默认使用带 `PipeUtilization` 的 Ascend `msprof`。
默认正确性模式是任务精度。只有要求每个提交 token 都和 stock target 一致时，
才使用 `lossless_token_forcing`。

以下配置缺失时直接使用默认值，不生成追问：

- performance policy：复用 `test_command` 或绑定的默认 benchmark
- accuracy policy：使用任务精度评测
- runtime parameter policy：从启动脚本发现可调参数
- startup feature policy：允许开启 vLLM-Ascend 中已有特性
- algorithm exploration：默认关闭，只评估启动参数和仓库已有特性
- source change policy：默认只调整启动参数，不修改源码
- source scope：`vllm_ascend/**` 和必要测试
- candidate order：启动参数 → 已有特性
- implementation provider：优先绑定的 `implementation_cmd`，否则按绑定的 provider 或宿主机可用的 `codex`、`claude` 顺序选择

没有安全默认值的字段才需要用户提供，例如投机方法、draft/MTP 权重位置、
启动脚本和测试定义。探索仓库中没有的算法属于显式授权项，不会默认开启。
用户确认后设置 `algorithm_exploration_policy: "user_authorized"`，或设置
`allow_unimplemented_algorithms: true`。该授权才会开放论文检索、第三阶段候选和源码实现。
兼容旧计划中的 `source_change_policy: "allow_source_changes"` 也视为明确授权。

## 默认优化顺序

用户没有指定顺序时，候选按以下顺序评估：

1. 修改启动参数。
2. 开启 vLLM-Ascend 中已经存在的特性。

只有用户明确授权算法探索时，才增加第三阶段。第三阶段会从仓库和论文中寻找当前代码没有的算法，生成最小源码补丁并隔离验证。

在执行 NPU 实验前，根据已测瓶颈、预期收益范围、运行时要求和实现成本对候选排序。
没有正向理论收益或缺少前置条件的候选不会进入实测。用户明确指定的顺序优先。

默认源码范围是 `vllm_ascend/**` 和必要测试。修改 `vllm/**` 需要用户明确授权。
实现卡片记录仓库版本、需要检查的文件和入口、算法步骤、tensor 约束、测试要求以及
unified diff 契约。代码生成器必须先在当前 checkout 中确认真实符号和调用链，再编写补丁。

## 默认投机长度搜索

默认运行时参数优化范围是 `num_speculative_tokens`。

- 从启动脚本读取当前 K。
- 如果解析失败，使用 `K=5`，并在 workload manifest 中记录
  `num_speculative_tokens_source=default`。
- 从当前值开始测试 `K-2` 和 `K+2`。
- 较大的 K 造成性能下降且算力利用率较高时，停止向上搜索。
- 较小的 K 造成性能下降时，停止向下搜索。
- 如果较小的正数 K 持续变好，在搜索到最小正数时测试 `K=0` 作为 AR baseline。
  其他情况下不测试 `K=0`，除非用户明确要求。

搜索没有固定最大值。当趋势、算力利用率、正确性门禁或用户目标表明继续搜索没有价值时停止。
用户可以指定固定轮数。此时执行完整预算，并返回所有轮次中通过全部门禁的最佳候选。

## 证据和结果比较

workload manifest 记录优化目标、数据集和模型版本、采样配置、预热次数、重复次数、
prompt ID、命令哈希和启动参数。每条请求记录输入输出 ID、token 数、延迟、TTFT、
解码速率、验证步数、接受 token 数、输出哈希和任务精度。

性能比较遵循用户的目标。吞吐使用配对速率比，延迟和 TTFT 使用用户指定的分位数，
显存使用峰值，接受率目标使用每次验证接受的 token 数。支持的指标会基于配对观测计算置信区间。

任务精度检查数据集上的最终答案，例如 GSM8K exact match。
Token forcing 检查相同 prompt 和前缀下，candidate 的每个 token 是否与 stock target 的决策一致。
最终答案可以相同，但中间 token 序列可能不同。只有要求严格 lossless 结果时才需要 token forcing，
其数据由运行时适配器生成，不要求用户手工填写 token ID。
如果服务接口无法提供 teacher-forcing 决策，则暂停并要求适配器或支持该能力的 endpoint 配置。

msprof 解析器支持 CSV 和 JSON 导出，能够归一化 draft、verify、scheduler、memory 阶段、
kernel 行、利用率、FLOP、字节数和数据质量标记。成本模型在有硬件峰值参数时，
计算每个 kernel 的保守算力和访存下界。缺少计数器会降低置信度，不能作为论文结论。

## 运行时绑定和源码实现

`rsi_install.py` 会幂等地创建 wrapper、参考适配器和
`config/live-runtime-bindings.json` 模板。绑定文件提供 doctor、profile、正确性、benchmark、
cleanup 以及可选源码实现的 argv 命令，同时提供环境变量、设备、超时和 Python 运行时。

如果允许修改源码，优先使用已配置的 `implementation_cmd`。
没有配置时可通过 `implementation_provider` 选择 `codex`、`claude` 或其他已绑定 provider，
未指定 provider 时按宿主机可用的 `codex`、`claude` 顺序探测。所有 provider 都接收同一份 JSON
候选输入，并且必须返回包含合法 unified diff 的结果。代码生成器不可用或补丁非法时，候选停止，
基线 checkout 不变。Claude Code 的非交互调用使用 print 模式，具体命令由绑定文件控制。

绑定示例：

```json
{
  "schema": "mindie-agent/rsi-bindings/v1",
  "default": {
    "runtime_entry": "bin/rsi_runtime_adapter.py",
    "runtime_python": "python3",
    "commands": {
      "profile_cmd": ["python3", "bin/profile.py"],
      "benchmark_cmd": ["python3", "bin/benchmark.py"],
      "cleanup_cmd": ["python3", "bin/cleanup.py"]
    },
    "implementation_provider": "claude-code",
    "implementation_providers": {
      "claude-code": {
        "command": ["claude", "-p", "--output-format", "json", "--input-format", "text"]
      }
    },
    "timeout_s": 3600
  }
}
```

## 知识搜索和经验沉淀

用户提出算法族时，默认只检索仓库和已配置知识源中的现有能力，根据实测瓶颈匹配候选，
一次最多返回六个候选。只有得到算法探索授权后，才会加入论文来源和仓库中尚未实现的算法。
每个候选包含适用原因、未知前置条件、保守收益范围、来源引用和实现请求。
适用候选不足六个时，刷新已配置知识源后继续搜索。

失败机制保存在本地 RSI memory 中，作为后续排除项，默认不重复实验。
只有通过配置的正确性和性能门禁的候选，才会进入知识沉淀队列。
知识记录描述特性在对应瓶颈、工作负载和硬件条件下的适用范围、限制条件、证据和来源引用，
不把某个投机长度作为通用结论。论文中的未验证收益不能代替本地门禁。

持久化文件位于选定的 RSI 状态目录中，包括 `autotune-state.json`、`workload-manifest.json`、
`active-best.json`、`memory.jsonl` 和 `knowledge-distill-queue.jsonl`。
远端知识提交支持重试和幂等操作，远端服务不可用时保留本地状态。
