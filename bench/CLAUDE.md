# bench — the JARVIS Capability Benchmark (JCB)

Loaded when you work under `bench/`. 108 tasks under `bench/tasks/`; `docs/how-to/run-the-benchmark.md` is the long form.

- Run against the deployed stack: start `bench/fixtures/server.py --port 8899`, then `python3 -m bench run --category <cat> --out bench/results/<name>`.
- The env vars are documented only in `bench/runner.py` `from_env`. `BENCH_EXEC="docker exec jarvis"` is required: without it the fixtures run on the host and the run fails in a misleading way.
- `python3 -m bench --replay-sessions N --models a,b` (`bench/replay.py`) also reads, with no value listed here:
  - `BENCH_MODELS_BASE`: the model server's base URL (else `BENCH_API_BASE`).
  - `BENCH_MODELS_KEY`: its key (else `BENCH_API_KEY`).
  - `BENCH_JUDGE_MODEL`: the judge model (else the second of `--models`).
- Approvals on production are `manual`: "0 approval requests" in a result means the tool was never called, not that it was allowed. Read the tool results in `state.db` before blaming tool choice.
