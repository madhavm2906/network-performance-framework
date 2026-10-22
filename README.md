# Network Performance and Regression Framework

A framework under development for running network benchmarks, validating
measurements, and comparing performance against recorded baselines.

## Implemented: command runner

- Executes an argument list without a shell.
- Captures UTF-8 stdout, stderr, return code, and elapsed command time.
- Records nonzero exits and timeouts as unsuccessful results.
- Reports missing executables as launch errors.
- On Linux, kills the command's process group on timeout and reaps the launcher.
  Workers must remain in the group; daemonized workers are outside this scope.
- Uses only the Python standard library.

Output is buffered in memory, so this version is intended for controlled
benchmark programs with small output. Elapsed command time includes startup
and cleanup and must not be used as the benchmark's network latency.

Linux with Python 3.12 is the target environment. Windows has a parent-process
timeout fallback; the Linux process-group test is skipped there.

## Run in Ubuntu from the project root

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 benchmarks/runner_smoke.py
```

Seven tests cover subprocess success and failure, working directories,
literal arguments, partial output during timeout, child-worker cleanup,
invalid configuration, and missing executables.

## Planned

Benchmark-specific adapters, correctness and measurement validation, saved
experiment metadata, baseline comparison, reports, and GitHub Actions.
The first planned adapter targets the collective-communication library.

No benchmark performance or regression-detection claims have been measured yet.
