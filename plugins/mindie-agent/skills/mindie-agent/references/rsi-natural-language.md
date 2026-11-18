# Natural-language RSI execution contract

The user may describe an optimization request in ordinary language. Convert it to a `mindie-agent/rsi-plan/v1` object and validate it with `scripts/rsi_plan.py` before any remote mutation. The plan is the hand-off between language understanding and the deterministic RSI runtime.

Required fields are:

```json
{
  "schema": "mindie-agent/rsi-plan/v1",
  "task_id": "decode-latency",
  "endpoint": {"host": "80.5.25.139", "port": 22, "user": "root"},
  "source_root": "/home/user/model",
  "rsi_root": "/home/user/RSI",
  "action_entry": "rsi-mindie/bin/mindie_agent_rsi_entry.py",
  "baseline": "baseline-control",
  "objective": "maximize verified decode throughput",
  "operations": ["doctor", "prepare", "build", "profile", "accuracy", "performance", "record", "cleanup"],
  "parallel_workers": 2,
  "devices": ["0", "1"],
  "bindings": "/home/user/RSI/config/live-runtime-bindings-prod.json",
  "payload": {"teacher_forced": true, "sealed_final": true, "paper_ready": true}
}
```

`endpoint` identifies the customer host for this run. `source_root` identifies the checkout being built, and `rsi_root` identifies the installed RSI runtime. `action_entry` is an optional relative path under `rsi_root`; it defaults to `bin/mindie_agent_rsi_entry.py`, and allows layouts such as the 139-host `rsi-mindie/bin/mindie_agent_rsi_entry.py` without changing the plugin. They are explicit because a later customer can use another host, checkout, container, or runtime path without changing this plugin. `bindings` is the per-run mapping of candidate URLs, source mounts, devices, and builder commands; it is deployment data, not a global plugin setting.

The conversational executor follows this order:

1. Ask for or discover the host, SSH port/user, source checkout, RSI runtime path, baseline, objective, and available device slots. Read-only discovery may use `remote_ls`, `remote_stat`, `remote_grep`, and `remote_exec_command`.
2. Produce and validate the plan. Reject missing paths, duplicate operations, arbitrary shell fields, and unknown operations. Never infer a customer path from the local machine.
3. Call `coordinator` to acquire an exclusive lease before `run`, `resume`, `profile`, `accuracy`, or `performance`. Reuse the returned `execution_id`; do not submit a second workload after an uncertain result.
4. Execute each rendered RSI step through `remote_exec_command` with the plan payload on stdin. Use `remote_write` for a payload file when the remote command needs a file; retain the returned `job_id` and poll it with `remote_job_status` / `remote_job_output`.
5. Require receipts from doctor, build, profiler, correctness scorer, and performance scorer. A failed or unknown receipt stops promotion and triggers `cleanup` after existing jobs are reconciled.
6. Promote `active-best` only when the runtime reports all requested gates (`benchmark_passed`, `correctness_passed`, `teacher_forced`, `paper_ready`, `sealed_final`) and the paired speed confidence interval clears the configured threshold. Otherwise report the exact failed gate and keep the baseline active.

The plan renderer is intentionally not an executor. It prevents the language layer from becoming a shell proxy while still allowing the existing MindIE MCP tools and the external RSI runtime to do the work.

For unattended multi-round search, run `scripts/rsi_autotune.py` on the selected RSI host with the validated plan. The controller expands `payload.candidates` or a bounded `payload.candidate_space`, runs each wave through the fixed RSI action entry, persists `autotune-state.json` and `autotune-queue.json`, and writes `active-best.json` only after the required gates and paired speed lower bound pass. A failed candidate is cleaned up; an `unknown`, `running`, or `submitted` receipt moves the loop to `needs_reconcile` and is never resubmitted automatically. `parallel_workers` controls bounded fan-out, while each candidate's binding must provide isolated devices and service ports when physical workloads run in parallel.

Example invocation after the plan has been written to the customer host:

```bash
python3 plugins/mindie-agent/scripts/rsi_autotune.py plan.json \
  --state-dir /home/customer/RSI/.mindie-agent/decode-latency \
  --parallel-workers 2 --max-rounds 8
```

The plugin remains host-neutral: `/home/customer/RSI`, the SSH endpoint, source checkout, devices, candidate URLs, and builder recipes all come from the plan and its per-run bindings. The controller does not copy the 139-host paths into the plugin.
