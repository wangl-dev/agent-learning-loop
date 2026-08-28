# ADR 0015: isolate local-model feasibility from action execution

## Status

Accepted for the M7C-A candidate on 2026-08-28. No real-model bundle is tracked by this ADR.

## Context

M7B publishes deterministic scripted-oracle development data, but that does not show whether a
small local model can consume the public task, tool schemas, and prior observations and emit one
strict action. Starting LoRA or replacing the scripted policy would mix template, parser, device
capacity, model choice, and Environment state divergence in one result.

## Decision

M7C-A probes only the next action before each assistant turn in the six validation trajectories.
Every prefix is attributed to the fixed public source commit in the packaged M7C-A contract and
contains the public instruction, constraints, scope, allowed tools, and the correct prior
action/result pairs. The current reference action is withheld from the prompt and used only by the
validator. Schema validation checks bounded string, object, and array JSON candidates even when
they appear inside prose, then compares action-shaped objects by tool and arguments. Canonical,
pretty-printed, reordered, or prose-wrapped encoded forms therefore still fail after prompt
re-signing. A complete JSON string is decoded at most two additional levels, so once- or
twice-encoded actions also fail without attempting arbitrary encodings. Message length, candidate
count, and traversed JSON nodes are bounded. Ordinary quoted prose is not treated as an action.
Test-task instructions, actions, results, and scope stay closed.

The backend boundary returns raw generation text and measurements. The Qwen3 adapter alone owns the
official chat template and optional Torch/Transformers imports. It fixes BF16, one CUDA device,
batch one, seed 17, sampling parameters, a 4,096-token input cap, and `trust_remote_code=False`.
The core wheel remains CPU/offline and has no model dependency.

The parser accepts exactly one Hermes-style `<tool_call>` wrapper with one JSON object, one known
allowed tool, strict existing Pydantic arguments, and public scope. It records format, tool,
argument, and reference mismatch separately. Predicted actions are never executed.

The read-only validator reconstructs every prompt, tool schema, reference, record, aggregate, and
report from the validation source Eval plus selected packaged public resources. It never calls a
model, Environment, Policy, tool, subprocess, database, or network service. Fake-backend bytes are
deterministic and externally fixed for CI. Real bundle hashes are internal consistency checks, not
a signature against someone able to replace every raw and derived file.

## Consequences

Qwen3-0.6B must complete the three-task smoke. Qwen3-1.7B receives the same BF16 CUDA attempt after
the first process releases GPU memory; a real OOM is kept as structured `capacity_blocked` evidence
with zero actions only when no generation has succeeded. OOM after a successful generation fails
closed and removes partial output. Completed Qwen records also receive model-library-free
finish/token/CUDA/VRAM consistency checks. The packaged model contract fixes Torch
`2.7.1+cu126` and Transformers `4.53.3`, while fake records retain their own `not-installed`
values. Capacity evidence uses the same strict schema when the runner writes and the validator
reads: total VRAM is positive, `free <= total`, and `allocated <= reserved <= total`. Current free
memory and historical peak-reserved memory are deliberately not added together because
they can describe different times. These checks reject impossible self-reports but do not turn
unkeyed bundle hashes into hardware attestation. Accuracy may be low
without failing M7C-A because this milestone establishes format, isolation, denominator, and local
capacity feasibility—not end-to-end success, speed, model selection, or training benefit.

The six-task/multi-seed canonical baseline, test split, live model-policy execution, task expansion,
SFT/DPO training, and model or adapter publication remain separate later decisions.
