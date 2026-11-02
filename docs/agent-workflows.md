# Agent workflows and roles

Detailed execution guidance for coding agents in `naeural_core`. The root
[`AGENTS.md`](../AGENTS.md) stays the concise contract. Roles are hats, not separate agents: one
agent usually wears several. Delegate only when a scope, tool or hardware boundary is clear.

## Roles

| Role | Owns | Escalate when |
| --- | --- | --- |
| Runtime | `main/`, `config/`; `start_nen.py` only when explicitly asked | exit or shutdown behavior, multiprocessing, deployment paths, or startup secrets change |
| Comms | `comm/`, `remote_file_system/`, `ipfs/` | wire format, topics, queue/retry semantics or broker assumptions change |
| Pipeline | `business/`, `data/`, `extensions/` | plugin loading or caching, queue overflow handling, or cross-subsystem config change |
| Serving | `serving/`, `heavy_ops/`, `local_libraries/model_server*`, `utils/tracing/` | CUDA, MinIO or model artifacts are needed, or latency regresses without explanation |
| Platform | `constants.py`, `core_logging/`, `utils/`, packaging | more than one subsystem is affected, or env keys or dependencies change |
| Docs | `docs/`, `_todo/`, `README.md`, `AGENTS.md` | sources of truth disagree, or a doc edit would hide a code defect |
| Critic | read-only review of a diff, tests and rollback | a claim cannot be verified, or a safety, data-loss or alert-noise risk remains |
| Integrator | runs tests and settles disagreements with evidence | results conflict or the environment cannot be reproduced safely |

## Single-agent loop

Use this by default, one concern per loop.

1. **Plan:** outcome, write scope, assumptions and the narrowest useful check.
2. **Implement:** the smallest coherent change.
3. **Test:** the narrow check first, then broaden with the regression risk.
4. **Critique:** environment feedback first (failures, logs, latency), then behavior,
   security, operator noise, rollback and maintainability.
5. **Revise:** only what evidence or a concrete risk justifies.
6. **Verify:** rerun the final checks and hand off changed files, results and open risks.

Escalate after two revisions of the same concern without new evidence, or three attempts that
leave the environment failing the same way.

## Actor-critic review

Use it when silent regression risk is high, or the change touches security, consensus, persistence
or operator-facing behavior.

1. The actor states scope, invariants, rollback and planned tests, then implements and tests.
2. The critic reviews independently and backs each finding with a failing command, a spec mismatch
   or a concrete scenario. Style preferences do not block.
3. The integrator settles disagreements by running the evidence, not by preference.
4. The actor fixes supported findings only, reruns the affected checks and records the result.

## Handoffs

A handoff or escalation names the goal, the current status (working, blocked, completed or
failed), changed files, each check as pass, fail or blocked with evidence, open risks and the next
recommended action. Fail closed: return the partial result and exactly what is missing.
