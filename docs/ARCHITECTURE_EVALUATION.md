# Blackboard SRE architecture evaluation

Evaluation date: **2026-09-29**

## What was actually executed

The complete unit suite was executed: **45 tests passed**. It covers blackboard
partition policy, hypothesis/evidence handling, blind specialist views, typed bus
rules, diagnosis gating, test-and-rollback behavior, live-adapter command safety,
budget accounting, submission ordering, artifact persistence, and installation and
campaign helpers.

The offline ablation runner was also executed across all six configurations
(`S0`, `M0`–`M4`) and its five synthetic problem labels, for 30 lifecycle runs.
That runner uses mock observations, canned specialist behavior, and mock mitigation;
it does not invoke a model, deploy a fault, compare a diagnosis with an oracle, or
ask SREGym to grade a repair. Its output now calls these values lifecycle completion
rates rather than “accuracy” or “success.” In this run, every multi-agent variant
produced a diagnosis and committed the synthetic mitigation, but **0% of M0–M4
diagnoses passed the diagnosis gate**; all used the offline driver's fallback. This
is useful evidence that the plumbing executes, but it is not evidence of incident
resolution quality or of M4 outperforming a baseline.

## Architecture assessment

### Verified strengths

- **Epistemic separation:** model-produced observations are ignored, evidence IDs
  must refer to runtime-created observations, and agents cannot mark hypotheses as
  verified.
- **Blind first pass:** the three diagnostic specialists receive restricted views
  before cross-examination, reducing direct hypothesis anchoring.
- **Fail-closed live diagnosis:** the live adapter does not submit a fallback or
  enter mitigation when the diagnosis gate cannot be satisfied.
- **Action containment:** diagnosis permits only read-only kubectl verbs. Mitigation
  enables a small write-verb allowlist, while shell composition, credential/context
  overrides, file inputs, deletion, and `kubectl exec` remain denied.
- **Bounded execution and auditability:** model calls, tool calls, and tokens have
  limits; requests, responses, tool evidence, submissions, state snapshots, and
  provider usage are written incrementally.

### Material limitations and risks

- **No live effectiveness result yet.** There is no valid diagnosis accuracy,
  mitigation success, time-to-diagnose, time-to-mitigate, or cost comparison from a
  real SREGym Lite campaign.
- **Offline ablations are not scientific comparisons.** The offline driver uses
  mock tools and deterministic agent responses. The problem labels do not change
  the injected telemetry, and all configurations therefore see essentially the same
  fixture.
- **The advertised full M4 design exceeds the live adapter.** Adaptive invocation,
  Jev routing, full TNR/durability semantics, and communication-fault experiments are
  not integrated end-to-end in the live path.
- **Repair coverage is intentionally narrow.** Some Lite incidents may require an
  operation outside the current allowlist. This is safer, but may lower mitigation
  coverage until additional operations have explicit validation and rollback.
- **Sequential specialists are expensive.** A normal diagnostic round invokes four
  roles, with up to six rounds and five turns per role. The hard budgets prevent
  unbounded execution, but a live campaign is still required to establish whether
  the added calls improve scores enough to justify their cost.

## Readiness verdict

The implementation is a **testable, safety-oriented prototype**, not a validated
high-performing SRE agent. Its strongest current evidence is policy enforcement and
protocol correctness. Claims about task-solving quality, model independence, lower
error propagation, or superiority of M4 over S0/M0 remain unproven until a live,
graded, repeated benchmark is run.

## Why the live 21-task campaign was not run here

This environment has no `OPENROUTER_API_KEY`, Docker, kubectl, Kind, or Helm. The
pinned SREGym repository is also absent, and cloning it was rejected by the network
proxy with HTTP 403. Therefore neither the Kubernetes benchmark environment nor real
OpenRouter inference was available. A synthetic dry run cannot be substituted for
that evaluation.

On a prepared host, export the key without writing it to disk, install the pinned
SREGym checkout, perform a one-task smoke test, and only then run all 21 tasks:

```bash
read -rsp 'OpenRouter API key: ' OPENROUTER_API_KEY && export OPENROUTER_API_KEY
uv run python scripts/install_sregym.py ../SREGym
uv run python scripts/run_campaign.py --sregym ../SREGym \
  --models qwen --problem network_policy_block --stages diagnosis
bash scripts/run_sregym_lite.sh ../SREGym qwen
```

Use `sregym_feedback.json` and the native result CSVs—not submission receipts or
offline lifecycle output—to calculate the real architecture metrics.
