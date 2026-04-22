# MP-08 Live Eval Baseline Attempt

Date: 2026-04-22
Repo: `/home/millionmax/git/enhanced-websearch`
Branch: `main`

## Outcome

Blocked. No live `enhanced-websearch` container was running, and nothing was listening on port `8091`, so the requested live `/search` and `/research` benchmark run could not be executed.

Machine-readable companion: `tests/fixtures/evals/baseline-results.json`

## Checks Performed

- Reviewed:
  - `plan/master-plan/30-mp-08-quality-gates-test-plan.md`
  - `tests/test_evals.py`
  - `tests/eval_gates.py`
  - `tests/fixtures/evals/`
- Checked Docker containers with `docker ps` and `docker ps -a`
- Searched for any container exposing `8091`
- Checked for any container named `enhanced-websearch`
- Confirmed `Dockerfile` and `docker-compose.yml` expect service/container `enhanced-websearch` on port `8091`
- Tried `curl http://127.0.0.1:8091/health` from the host; connection failed

## Docker Findings

- `docker ps` showed multiple running containers, but none named `enhanced-websearch`
- No running or stopped container matched `enhanced-websearch`
- No container exposed `8091`
- `docker compose` and `docker-compose` CLIs were not available in this environment, so I did not attempt to start the stack

## Planned Representative Fixture Set

These were selected as a good 7-case live benchmark slice once the service is available:

1. `tests/fixtures/evals/factual_python_release_date.json`
   - factual lookup
   - endpoint: `/search`
2. `tests/fixtures/evals/comparison_postgres_mysql_analytics.json`
   - comparison
   - endpoint: `/research`
3. `tests/fixtures/evals/howto_fix_docker_permission_denied.json`
   - technical how-to
   - endpoint: `/research`
4. `tests/fixtures/evals/broad_explainer_http2_http3.json`
   - broad explainer
   - endpoint: `/research`
5. `tests/fixtures/evals/contradiction_remote_work_productivity.json`
   - contradiction-heavy
   - endpoint: `/research`
6. `tests/fixtures/evals/recency_python_release_research.json`
   - recency-sensitive
   - endpoint: `/research`
7. `tests/fixtures/evals/sparse_evidence_local_rag_regulated_docs.json`
   - sparse-evidence
   - endpoint: `/research`

## Gate Expectations Once Unblocked

The intended scoring path is to pass each captured live JSON response through the gates in `tests/eval_gates.py`:

- `generic_answer`
- `thin_answer`
- `grounding_citation`
- `contradiction_handling`
- `unsupported_certainty`
- `regression_baseline`

Because no live responses were captured, there are no pass/fail scores yet.

## Baseline Status

Current baseline is not acceptable as a live starting point yet, because there is no runnable backend instance to generate real benchmark data.

## Unblock Requirements

Before rerunning this task, ensure one of the following is true:

- a container named `enhanced-websearch` is running and serving on port `8091`, or
- an equivalent running container is identified and reachable for `docker exec` plus local POSTs to `/search` and `/research`

Once that is available, the selected fixture set can be run and scored quickly.
