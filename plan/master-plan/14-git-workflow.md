# Git Workflow

Use git as the execution backbone for multi-agent development.

## Core rule

All meaningful work should happen on local git branches with committed checkpoints.

Do not rely on large uncommitted working trees.
Do not rely on origin pushes for normal execution.

## Goals

- isolate parallel work
- keep changes reviewable
- make rollback easy
- prevent subagents from stepping on each other
- preserve an audit trail of implementation progress

## Branch model

### Stable baseline

- `main` = local stable baseline

### Milestone branches

Use one branch per milestone or subtask.

Examples:

- `mp-01-search-hardening`
- `mp-01-router-cooldowns`
- `mp-01-search-contract`
- `mp-03-research-synthesis`
- `mp-04-vane-integration`

## Subagent rule

Each dev subagent should work on its own branch.

If parallel work is needed:

- split by milestone or subtask
- avoid overlapping file ownership when possible
- reconcile only after review

## Commit rule

Commit early when the unit of work is coherent.

Good commit examples:

- `mp-01: tighten provider cooldown and fallback logic`
- `mp-01: preserve perplexity search response shape`
- `mp-03: synthesize research summary from grounded evidence`
- `mp-04: surface vane output in diagnostics`

## Review rule

A branch is not considered complete because code exists.

Completion requires:

- implementation commit(s)
- test/review pass
- no blocking issues found in review

## Merge rule

Merge locally only after:

- dev work is complete
- tests/review pass
- branch is reviewed against milestone scope

## No automatic origin pushes

Do not push to origin unless explicitly requested.

The default workflow is:

- local branches
- local commits
- local merge after review

## Suggested milestone execution pattern

1. create branch for milestone/subtask
2. delegate dev work on that branch
3. commit changes
4. run independent test/review work
5. merge locally when accepted
6. update `13-status-tracker.md`

## Working agreement

Git is part of the execution process, not just a backup mechanism.
