# Natural-language RSI execution contract

The user may describe an optimization request in ordinary language. Convert it to a `mindie-agent/rsi-plan/v1` object and validate it with `scripts/rsi_plan.py` before any remote mutation. The plan is the hand-off between language understanding and the deterministic RSI runtime.

Required fields are:

```json
{
  "schema": "mindie-agent/rsi-plan/v1",
  "task_id": "decode-latency",
  "endpoint": {"host": "<remote-host>", "port": 22, "user": "root"},
  "source_root": "/home/user/model",
  "rsi_root": "/home/user/RSI",
  "startup_script": "/home/user/model/start.sh",
  "test_command": ["vllm", "bench", "serve", "--model", "/models/foo", "--num-prompts", "128"],
  "baseline": "baseline-control",
  "objective": "maximize verified decode throughput",
  "operations": ["doctor", "prepare", "build", "profile", "accuracy", "performance", "record", "cleanup"],
  "parallel_workers": 2,
  "devices": ["0", "1"],
  "bindings": "/home/user/RSI/config/live-runtime-bindings-prod.json",
  "payload": {"teacher_forced": true, "sealed_final": true, "paper_ready": true}
}
```

`startup_script` is required for reproducing the baseline service. The plan
must also provide one test definition: `test_script`, `test_config`,
`test_description`, or `test_command`. A script/config is a remote artifact;
`test_description` is a natural-language workload definition such as “run the
vLLM serving benchmark with batch size 8, 128 input tokens, and 256 output
tokens”; `test_command` is a direct argv array (or shell-free command string)
such as
`["vllm", "bench", "serve", "--model", "/models/foo", "--num-prompts", "128"]`.
The command is executed without a shell. The orchestration layer resolves a
description to the MindIE/vLLM benchmark available on the target host and asks
for clarification if the description is ambiguous; it never invents a passing
receipt. If the startup script or all test definitions are missing, planning
stops with a structured `needs_input` result and asks the user for the missing
input. A prose metric such as `1000 tokens/s` remains an observation, not a
reproducible test definition.
The remote RSI loop also checks that the supplied files exist before creating
any candidate or starting a workload. Missing files return `needs_input` with
the exact field and path to provide.

`endpoint` identifies the customer host for this run. `source_root` identifies the checkout being built, and `rsi_root` identifies the installed RSI runtime. During installation, `scripts/rsi_install.py` creates the stable `<rsi_root>/bin/mindie_agent_rsi_entry.py` wrapper, a reference `<rsi_root>/bin/rsi_runtime_adapter.py`, and an absent `config/live-runtime-bindings.json` template. The wrapper reads the selected customer's `runtime_entry` and `runtime_python` from that binding file, so a layout such as the customer-host `rsi-mindie/bin/mindie_agent_rsi_entry.py` is handled without putting its path in the plan. If no existing runtime is found, the generated binding selects the reference adapter. `bindings` is the per-run mapping of candidate URLs, source mounts, devices, and builder commands; it is deployment data, not a global plugin setting.

The conversational executor follows this order:

1. Ask for or discover the host, SSH port/user, source checkout, RSI runtime path, baseline, objective, and available device slots. Read-only discovery may use `remote_ls`, `remote_stat`, `remote_grep`, and `remote_exec_command`.
2. Produce and validate the plan. Reject missing paths, duplicate operations, arbitrary shell fields, and unknown operations. Never infer a customer path from the local machine.
3. Call `coordinator` to acquire an exclusive lease before `run`, `resume`, `profile`, `accuracy`, or `performance`. Reuse the returned `execution_id`; do not submit a second workload after an uncertain result.
4. Execute each rendered RSI step through `remote_exec_command` with the plan payload on stdin. Use `remote_write` for a payload file when the remote command needs a file; retain the returned `job_id` and poll it with `remote_job_status` / `remote_job_output`.
5. Require receipts from doctor, build, profiler, correctness scorer, and performance scorer. A failed or unknown receipt stops promotion and triggers `cleanup` after existing jobs are reconciled.
6. Promote `active-best` only when the runtime reports all requested gates (`benchmark_passed`, `correctness_passed`, `teacher_forced`, `paper_ready`, `sealed_final`) and the paired speed confidence interval clears the configured threshold. Otherwise report the exact failed gate and keep the baseline active.

The plan renderer is intentionally not an executor. It prevents the language layer from becoming a shell proxy while still allowing the existing MindIE MCP tools and the external RSI runtime to do the work.

### Defaults and follow-up questions

When the user does not name a profiling tool, the plan binds the profiling
policy to `msprof` with `PipeUtilization`; the installed container or PATH
resolves the executable. This is a tool selection default, not permission to
invent a customer command line. The orchestration layer asks whether a custom
performance command or correctness command should be used. Choosing the
default performance policy reuses `test_command` (or the supplied test script
adapter). Choosing the default accuracy policy reuses the runtime's accuracy
binding; if that binding is absent, execution pauses with `needs_input`.
The default correctness mode is `task_accuracy`, which evaluates the task's
answer metric. Choose `lossless_token_forcing` when every committed token must
match the stock target, for example when reproducing the paper's lossless
claim.

If the user does not state an optimization scope, ask whether to use the
default first pass. The default scope tunes
`num_speculative_tokens` with a bidirectional trend-bound strategy. The
controller parses the current value from the supplied startup script, tests adjacent values
at a step of two on both sides, and stops a direction at its first measured
loss. If the startup script cannot be parsed, the manifest records `K=5` with
`num_speculative_tokens_source=default` and uses it as the search origin. A higher-K loss supported by high compute utilization stops the upward
direction. A lower-K loss stops the downward direction. The controller tests
`K=0` only when every measured positive K improved while moving downward, or
when the user requests an autoregressive baseline.

For a speculative-decoding request, ask two separate questions when they are
not present: which method (`mtp`, `dflash`, `dflash2`, or `dspark`) and where
the target/draft/MTP weights are located. The weight path can usually be read
from the startup script's `--speculative-config` or model arguments. A request
such as “optimize SSM and dynamic speculation” should be kept at the intent
level first: record `algorithm` scope with requested features, discover the
runtime's supported flags, then present the concrete candidate knobs for
approval. The agent should not silently map SSM or dynamic speculation to a
different method or weight directory.

The structured question generator is available as `rsi_defaults.questions`.
It returns choices and defaults for the language layer, so a user can answer
in ordinary language, for example: “用默认 profiling，性能用现有 vLLM
命令，正确性用默认 GSM8K exact-match，只调 num_speculative_tokens”。

The initial plan also asks which startup parameters may be changed and whether
source changes are allowed. Parameter-only plans discover tunable flags from
the supplied startup script when the user does not provide a list. When source
changes are allowed, RSI inserts the `implement` stage automatically. MindIE
Agent provides knowledge search and experiment orchestration, but it does not
contain a general-purpose code generation model. The implementation stage first
uses an explicitly bound `implementation_cmd`; if none is configured it tries
the host `codex exec` command. If Codex is unavailable or cannot return a
valid unified diff, the candidate stops with `needs_input` and asks for a code
generator binding.

Source implementation defaults to the smallest vLLM-Ascend change. The
implementation card prefers `vllm_ascend/**` and `tests/**`, marks
`vllm/**` as forbidden unless explicitly authorized, and requires a minimal
unified diff that follows the selected checkout's existing style. The source
scope question can be answered with `allow_vllm_core_if_required` when an
Ascend-side extension point is insufficient. The implementation generator must
justify every file outside the default scope and include the corresponding
test or compatibility reason.

The plan also asks whether existing repository features may be enabled from the
startup script. The default candidate order is runtime parameter changes,
existing feature switches, and source changes that require a new
implementation. Before an NPU run, each candidate receives a theoretical
precheck using the measured bottleneck, expected gain range, runtime
requirements, and implementation cost. Candidates with no positive expected
gain or unsatisfied requirements are not measured. An explicit user order
overrides the default order.

The installation step can be run explicitly on the customer host:

```bash
python3 plugins/mindie-agent/scripts/rsi_install.py /home/customer/RSI
```

It creates the standard wrapper, reference adapter, and binding template only when those files are absent. Fill the generated `config/live-runtime-bindings.json` with the host-specific command arrays, source/build paths, device lease settings, and Python environment; existing files are preserved. `rsi_autotune.py` performs the same idempotent installation automatically before its first real action.

The binding file keeps deployment details out of the plan:

```json
{
  "schema": "mindie-agent/rsi-bindings/v1",
  "default": {
    "runtime_entry": "bin/rsi_runtime_adapter.py",
    "runtime_python": "python3",
    "commands": {
      "doctor_cmd": ["python3", "bin/customer_doctor.py"],
      "profile_cmd": ["python3", "bin/customer_profiler.py"],
      "teacher_forcing_scorer_cmd": ["python3", "bin/customer_teacher_forcing.py"],
      "benchmark_cmd": ["python3", "bin/customer_benchmark.py"],
      "cleanup_cmd": ["python3", "bin/customer_cleanup.py"]
    },
    "env": {"ASCEND_RT_VISIBLE_DEVICES": "0,1"},
    "timeout_s": 3600
  }
}
```

When `test_command` is present and the runtime binding does not define a
`benchmark_cmd`, the adapter uses `test_command` for the `performance` step.
This accepts a ready-to-run vLLM command while keeping execution shell-free.
For `test_description`, the orchestration layer must first resolve the prose
into a concrete MindIE/vLLM argv command; the low-level adapter will not guess
one or mark an unresolved description as passed.

For unattended multi-round search, run `scripts/rsi_autotune.py` on the selected RSI host with the validated plan. The controller first installs the standard wrapper without overwriting an existing entry, then expands `payload.candidates` or a bounded `payload.candidate_space`, runs each wave through the binding-selected runtime entry, persists `autotune-state.json` and `autotune-queue.json`, and writes `active-best.json` only after the required gates and paired speed lower bound pass. A failed candidate is cleaned up; an `unknown`, `running`, or `submitted` receipt moves the loop to `needs_reconcile` and is never resubmitted automatically. `parallel_workers` controls bounded fan-out, while each candidate's binding must provide isolated devices and service ports when physical workloads run in parallel.

Example invocation after the plan has been written to the customer host:

```bash
python3 plugins/mindie-agent/scripts/rsi_autotune.py plan.json \
  --state-dir /home/customer/RSI/.mindie-agent/decode-latency \
  --parallel-workers 2 --max-rounds 8
```

By default, a plan with `target_speedup` (or `min_speedup`) stops as soon as the promoted best candidate reaches that target and returns the best candidate that passed all required gates. If the user explicitly requests a fixed budget, for example `--max-rounds 20`, the controller runs that budget even after the target is reached and returns the best candidate observed across all rounds. An unknown receipt or an exhausted queue still stops early and is reported for reconciliation.

The plugin remains host-neutral: `/home/customer/RSI`, the SSH endpoint, source checkout, devices, candidate URLs, runtime entry, and builder recipes all come from installation/runtime bindings. The plan does not copy the customer-host paths into the plugin.

## Paper-loop capabilities imported from an existing RSI runtime

The controller also contains the reusable parts found in the customer-host
`/home/<user>/RSI` tree, without copying that host's paths or runtime
commands into the plugin:

- `scripts/rsi_cost_model.py` consumes repeated profiler observations and
  writes `cost-model.json` with a conservative ceiling, acceptance statistics,
  stage fractions, confidence, evidence gaps, and the largest bottleneck.
- `scripts/rsi_workload.py` turns the supplied test definition into a
  privacy-safe workload manifest with objective metric, dataset and model
  revisions, prompt selection, decoding settings, warmup/repeat policy, and
  command hashes. Host paths and endpoint values are excluded from the
  manifest snapshot.
- `scripts/rsi_compare.py` compares paired candidate and baseline rows using
  the user's objective. Throughput uses paired rate ratios, latency uses the
  requested percentile, memory uses peak usage, and accepted-token objectives
  use accepted tokens per verify step. Bootstrap confidence intervals are
  applied to rate ratios when the objective supports them.
- `scripts/rsi_proposals.py` turns that bottleneck into a bounded, deterministic
  shortlist when `payload.auto_propose` is enabled. It emits candidate data;
  build and deployment remain the binding-selected runtime's responsibility.
- `scripts/rsi_workspace.py` applies a unified diff in a detached git worktree
  using argv-only git calls and removes the worktree on failure. This keeps a
  source checkout reusable across customers.
- `scripts/rsi_memory.py` stores mechanism, requirements, correctness contract,
  performance evidence, applicability, and limits in an append-only JSONL file.
  Matching entries are included in the next candidate payload for replay.
- `scripts/rsi_sealed_final.py` validates independent unseen prompts, long
  outputs, holdout disjointness, strict teacher-forced token rows, and a
  bootstrap paired-speed CI. If `sealed_final_payload` is supplied, the
  controller uses this report for the sealed-final and speed gates; missing
  evidence fails closed.
- `scripts/rsi_runtime_adapter.py` is the install-time fallback adapter. It
  maps `doctor`, `prepare`, `build`, `profile`, `accuracy`, `performance`, and
  `cleanup` to binding-selected argv commands, persists action receipts, and
  propagates profiler/scorer/benchmark evidence without inventing gates.
- `scripts/rsi_evidence.py` normalizes the paper evidence contract. In strict
  mode it requires at least five profile observations with elapsed/token and
  draft/verify/scheduler/memory stage fields, five paired performance rows,
  and token-forcing rows with position, teacher-forced, and match/mismatch
  fields. `rsi_sealed_final.py` and `rsi_autotune.py` use these checks before
  speed CI or active-best promotion.

Strict paper plans should set `strict_evidence: true`,
`min_profile_samples: 5`, and `min_paired_runs: 5` in the payload. These
defaults can be raised for a customer's evaluation protocol but should not be
lowered for a paper claim.

## Knowledge-card algorithm search

The adaptive `num_speculative_tokens` search is only the default when the
optimization scope is omitted or the user selects runtime-parameter tuning. It
is not the complete speculative-decoding optimizer. When the request names an
algorithm family (for example DFlash/DFlash2, MTP, DSpark, SSM drafting,
dynamic speculation, acceptance-aware scheduling) or selects
`optimization_scope.kind="algorithm"`, `auto_propose` enters the knowledge
branch:

1. Refresh due configured knowledge feeds and query repository/paper cards. The
   installed knowledge feed scheduler owns the interval and backoff; the RSI
   payload records `knowledge.refresh` and does not silently scrape arbitrary
   URLs.
2. Normalize cards into `rsi-knowledge/v1` and rank them against the measured
   bottleneck, acceptance data, runtime capabilities, and the user's intent.
3. Return up to six candidates. If fewer than six applicable cards remain, the
   controller refreshes the sources and searches again. Failed mechanisms are
   recorded in RSI memory and excluded from later waves unless the user
   explicitly asks to retry them. Each candidate contains the applicability
   reasons, unknown requirements, source references, a conservative expected
   gain range, and an implementation request. A paper's reported speedup is a
   prior for ordering only; it never satisfies a local gate.
4. Generate or apply a patch in the isolated RSI worktree, then run build,
   profiling, correctness, paired performance, and sealed-final gates. Failed or
   unknown receipts remain visible and are never retried automatically.

For source-level candidates, the runtime binding may provide
`implementation_cmd` (or `implement_cmd`). It receives the complete candidate,
knowledge references, bottleneck report, and validation plan as JSON on stdin,
and must return JSON containing a unified `patch`. RSI applies that patch to a
detached worktree before invoking build and the remaining gates. A missing or
invalid patch fails that candidate and leaves the baseline checkout untouched.

Cards may be supplied from `knowledge_query`/`knowledge_explain` as
`knowledge_cards`, `foundation_cards`, `cards`, or `units`; built-in navigation
cards cover the common speculative families until the configured feeds return
more specific, revisioned evidence. New cards are distilled only from
authorized, source-located evidence and verified experiments
(`auto_distill="verified_sources_only"`). This keeps repository and academic
material useful for discovery without turning an unverified claim into a
deployment change.

Every RSI result is written to append-only memory. Only a promoted result that
passed the configured correctness and performance gates creates a
`knowledge-distill-queue.jsonl` request. The request contains the mechanism
family, applicability, requirements, correctness gates, performance receipts,
limits, source references, and an `rsi://` source locator. The knowledge
service consumes these requests through `knowledge_distill` at
`verified_milestone` or `completed`, compares them with related cards, merges
the result into the existing mechanism family, and maintains the resulting
card in the configured knowledge repository. Failed build, correctness,
infrastructure, and performance mechanisms remain in local RSI memory as
exclusions and are not distilled into knowledge cards.

These modules are deliberately evidence-driven. They do not invent a passing
result when the customer runtime has not returned profiler, scorer, and paired
benchmark rows. The reference adapter now has built-in token-row scoring and
Ascend profile normalization when `token_forcing_rows` or `profile_paths` are
present in the action payload. A customer command remains useful when token
generation or model execution must happen outside the adapter. The historical
`/home/<user>/RSI` scripts are one implementation of those commands, not a
required path.

The profile normalizer accepts msprof CSV or JSON exports and produces
`profile_observations`, stage fractions, kernel rows, FLOP/byte counters, and
profile quality flags. The cost model consumes those fields, applies a
per-kernel `max(FLOPs/peak_FLOPs, bytes/peak_bytes)` floor when hardware peaks
are supplied, and folds optional tree-replay and launch-overhead evidence into
the ceiling estimate. Missing counters reduce confidence and never become a
measured paper claim.

Token forcing data is an internal runtime artifact. The user does not type
token ids or assemble `token_forcing_rows`. The selected accuracy adapter runs
the candidate and stock target with the same prompts and decoding settings,
then passes aligned token ids to `rsi_token_forcing.py`. A deployment-specific
adapter is still needed when the serving API cannot expose teacher-forced
target decisions. In that case the plan pauses for an adapter command or a
supported endpoint configuration, rather than asking the user to provide raw
token rows.

Task accuracy and token forcing answer different questions. GSM8K exact-match,
BLEU, pass@k, or another task scorer checks the final answer on a dataset.
Token forcing checks every committed candidate token against the stock target's
decision for the same prefix. A task score can pass while an intermediate
token differs and later text happens to reach the same answer, so strict
lossless promotion requires both gates when the plan requests paper-style
correctness.

Successful RSI entries are appended to a durable distillation queue with an
idempotency key. When `auto_distill` is enabled, the queue converts the RSI
request to the native `knowledge_distill` schema and submits it through the
installed `knowledge_cli.py`. Retryable transport or validation failures stay
in the queue. A timeout or an explicitly unconfirmed remote outcome is marked
`unknown` and is never replayed automatically, preserving the remote mutation
boundary and the local evidence.

Each selected candidate also carries an `implementation-card/v1`. Codex
receives the repository and runtime revision, search queries, preferred
directories (`vllm/spec_decode`, `vllm_ascend`, and `tests` by default), entry
points to locate, algorithm steps, tensor shape/dtype/device constraints,
allowed and forbidden file changes, unit/correctness/performance tests, and the
unified-diff output contract. The card is a starting map for repository
exploration. Codex must verify the actual call chain and symbols in the
selected checkout before writing the patch.
