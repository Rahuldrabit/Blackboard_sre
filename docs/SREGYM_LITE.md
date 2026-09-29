# SREGym Lite on a cloud Linux VM

This repository contains an initial live Blackboard adapter plus a serial campaign
runner for **Qwen3.5 Flash** and **DeepSeek V4 Flash** through OpenRouter.

**Current verification:** the offline protocol dry run covers all 21 upstream Lite
IDs for both model configurations (42 combinations). These tests use synthetic model
responses and synthetic tool evidence; they do not deploy the applications, inject
real faults, call OpenRouter, or establish that an agent can solve a task. See
[the recorded dry-run report](dry_run_report.json).

The original `clients.graphstate.driver.driver` and `scripts/run_experiments.py`
remain **offline simulations**, not benchmark runners. Use the commands here for
real SREGym evaluation.

## 1. Prepare a cloud VM

Use a Linux VM with Docker, at least 8 vCPU and 16 GB RAM for Lite, Python 3.12+,
uv, kubectl, Helm 4+, and Kind. Follow the upstream
[SREGym installation guide](https://github.com/SREGym/SREGym) and its
[Kind guide](https://github.com/SREGym/SREGym/tree/main/kind).
The scripts here are provider-independent: AWS, Azure, GCP or another Linux VM
can host the same Kind setup. This does not provision cloud resources.

Clone both repositories into the same parent directory and use the tested SREGym
revision. The applications repository is a pinned SREGym submodule; do not replace
it with an unrelated latest checkout.

```bash
git clone https://github.com/Rahuldrabit/Blackboard_sre.git
git clone https://github.com/SREGym/SREGym.git
cd SREGym
git checkout "$(cat ../Blackboard_sre/sregym-version.txt)"
git submodule update --init --recursive
uv sync
cd ../Blackboard_sre
uv sync --extra dev
uv run python scripts/install_sregym.py ../SREGym
```

The installer copies `clients/graphstate`, registers it with SREGym under the
**`blackboard-sre`** agent name, and makes one host-side SREGym change:
`SREGYM_RESULTS_DIR` can select the result root. Other agent registrations and their
comments are retained; the campaign command always passes `--agent blackboard-sre`,
so no pre-existing SREGym agent is selected. An installation
manifest permits updates but rejects manual edits to the installed copy. The
campaign runner checks that the installed source matches this repository.

## 2. Dry run all 21 tasks, both models

This command needs no Docker daemon, Kubernetes cluster or API key:

```bash
uv run python scripts/run_campaign.py --sregym ../SREGym --dry-run
uv run --extra dev pytest tests/unit -q
```

It checks suite IDs against the upstream registry, application submodule presence,
agent registration/isolation, source syntax, installed-source equality and separate
result-directory support. For each selected model/task pair it exercises the actual
Blackboard adapter's diagnosis gate, tool accounting, stage transitions and logging
against a synthetic HTTP/MCP/model fixture. It checks the integration protocol;
**every task uses the same synthetic fixture, not a simulation of its real fault**.

Dry-run artifacts are kept under `artifacts/dry-run/` and cannot be confused with
live score files. The machine's clock and all campaign dates use UTC.

## 3. Start the benchmark cluster

Only do this for a live run, after Docker and the Kubernetes tools are installed:

```bash
cd ../SREGym
docker info
bash kind/setup_kind_cluster.sh
kubectl get nodes
cd ../Blackboard_sre
```

Use the dedicated SREGym cluster. Keep campaigns serial on one cluster. The runner
locks its SREGym checkout to prevent overlapping campaigns from this runner;
separate checkouts must not point simultaneous campaigns at the same cluster.

## 4. Configure OpenRouter and start with one task

Set the key in the cloud VM's shell; do not paste it into source or commit it:

```bash
read -rsp 'OpenRouter API key: ' OPENROUTER_API_KEY
export OPENROUTER_API_KEY

# Diagnosis-only smoke test with real inference and real grading:
uv run python scripts/run_campaign.py --sregym ../SREGym \
  --models qwen --problem network_policy_block --stages diagnosis
```

The exact agent model IDs are:

- `openrouter/qwen/qwen3.5-flash-02-23`
- `openrouter/deepseek/deepseek-v4-flash`

These use the [Qwen](https://openrouter.ai/qwen/qwen3.5-flash-02-23) and
[DeepSeek](https://openrouter.ai/deepseek/deepseek-v4-flash) OpenRouter IDs with the
LiteLLM `openrouter/` prefix. No model inference was performed during local testing.

Both campaigns default to the same Qwen judge for consistency. To use a different
judge, pass `--judge-model openrouter/<provider>/<model>` or set
`BLACKBOARD_JUDGE_MODEL`. Agent and judge inference both incur provider charges.

## 5. Run the full campaigns

```bash
# All 21 tasks for Qwen, then all 21 for DeepSeek:
uv run python scripts/run_campaign.py --sregym ../SREGym

# Or one model at a time:
bash scripts/run_sregym_lite.sh ../SREGym qwen
bash scripts/run_sregym_lite.sh ../SREGym deepseek
```

The first command is the complete 42-run matrix (21 Lite tasks × 2 models). To run
all 21 tasks once, use either of the one-model shell commands. The OpenRouter key is
read only from `OPENROUTER_API_KEY`; it is forwarded to the isolated Blackboard SRE
container as the agent and judge credential and is never written to a manifest or
command line. Do not pass the key as a command-line argument.

Each task starts a separate SREGym process. Both stages run by default. The first
process rebuilds the isolated agent image, and subsequent tasks reuse it.
`--agent-timeout 1800` controls SREGym's agent-phase timeout. `--profile full` is the
default; a `svelte` run has different infrastructure and should be labelled as such.

A nonzero SREGym process exit stops the campaign so the next task does not start on
an unclean cluster. A recorded failed diagnosis/mitigation is an evaluation outcome,
not necessarily a process error. Completed logs remain available after failures.
The runner does not automatically resume; rerun a specific `--problem` after checking
the cluster. Every invocation creates a unique run directory and preserves earlier runs.

## Where all logs and feedback are saved

Paths are relative to this repository unless you pass `--results-dir /persistent/disk/sre`.
Store that directory on persistent VM storage and download/archive it before deleting
the VM. Runtime logs are gitignored and **are not pushed to GitHub**.

```text
artifacts/
  live/2026-09-29/
    campaign_<time>_<id>.json                  # all task outcomes and exact commands
    openrouter_qwen_qwen3.5-flash-02-23/
      network_policy_block/<time>_<id>/
        manifest.json                        # task, model, judge, revision, command
        outcome.json                         # return code, timestamps, artifact path
        console.log                          # combined harness/agent stdout + stderr
        judge_usage.jsonl                    # host backend usage, when upstream emits it
        host_logs/                           # native SREGym host logging
        sregym_feedback.json                 # complete native CSV rows as JSON
        sregym/<upstream-timestamp>/
          blackboard-sre_ALL_results.csv     # official SREGym scores and feedback
          blackboard-sre/network_policy_block/
            phases_attempt1.jsonl            # native lifecycle timing, when emitted
            run_1/
              network_policy_block_results.csv
              internet_audit.json
              graphstate/<agent-run-id>/
                events.jsonl                 # every adapter event, written incrementally
                tools.jsonl                  # tool requests, raw results, failures
                submissions.jsonl            # submissions and observed stage status
                usage.jsonl                  # real per-call provider usage
                state.json                   # latest Blackboard snapshot
                agents/
                  topology.jsonl             # prompts, responses, usage for Agent A
                  telemetry.jsonl            # Agent B
                  config_system.jsonl        # Agent C
                  falsifier.jsonl            # cross-examination
                  mitigation_planner.jsonl   # Agent D
    openrouter_deepseek_deepseek-v4-flash/
      <task>/<time>_<id>/...
  dry-run/<UTC-date>/...                      # synthetic fixtures, no real scores
```

`SREGym_feedback.json` is spelled **`sregym_feedback.json`** on disk. It preserves
all columns upstream writes: diagnosis verdict, reasoning, checklist/dimensions,
mitigation success/failure, timeout and cleanup information when available. Native
CSVs remain untouched. A submission receipt means the harness accepted a submission;
it is **not** a success score. Early failures may have no native CSV yet; console
and incremental agent logs remain the debugging source. If SREGym stops before
publication, partial agent logs remain inside `sregym/.../.runtime/`.

The real task name is assigned by the host runner. Inside the evaluated container,
SREGym uses opaque artifact IDs, so log organization does not expose fault names or
oracle answers to the agent. All specialists use the selected model for that run.

## Current limits

This is an initial live adapter, not the full M4 research implementation described
in the original architecture README. It reuses the specialist prompts, blind views,
state schemas, patch validation and diagnosis gate. It does not yet implement the
complete ablation matrix, adaptive/Jev routing, tiered models, or communication-fault
experiments. The existing causal verifier uses heuristic checks, not a causal proof.

Diagnosis permits read-only kubectl operations and Prometheus/Jaeger queries.
Mitigation additionally permits `patch`, `set`, `scale`, restricted `rollout` and
SREGym MCP rollback tools. Shell composition, `kubectl exec`, deletion and file-based
manifests are unsupported in this adapter. Some real Lite repairs may require these
operations and remain unsolved. The dry run does not validate repair coverage.
SREGym's real rollback stack is used; the offline TNR simulator is not. Automatic
rollback, durability proof and universal task success are not claimed.

Only runtime tool output creates observations. Diagnosis requires fresh, distinct
queries with valid references. Provider failures, invalid JSON, missing usage and
exhausted budgets never silently fall back to mock responses. The token budget is
checked after each response, so its final response can overshoot the threshold.
