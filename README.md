# Network Performance and Regression Framework

[![CI](https://github.com/madhavm2906/network-performance-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/madhavm2906/network-performance-framework/actions/workflows/ci.yml)

A Python framework for running network benchmarks, validating timing samples,
saving experiment evidence, and detecting threshold-based latency regressions.

Includes a standalone C++17 TCP round-trip benchmark for Linux and an optional
adapter for the separate collective-communication project.

## Implemented features

- Command execution without a shell.
- Captured stdout, stderr, return code, and total command time.
- Whole-command timeouts with Linux process-group cleanup.
- Standalone TCP request/echo benchmark with payload verification.
- Independent benchmark processes with warmup and measured iterations.
- Validation of worker identity, settings, verification status, and samples.
- Saved raw output, validated samples, execution details, and experiment metadata.
- Executable SHA-256 and clean-source Git revision recording.
- Baseline comparison using relative and absolute slowdown thresholds.
- Separate checks for typical latency and tail latency.
- GitHub Actions tests, smoke experiments, comparison tables, and evidence artifacts.

## Architecture

```text
C++ TCP benchmark
    |
    | JSON timing samples
    v
Python command runner
    |
    | captured output and execution status
    v
TCP experiment recorder
    |
    | validated samples, metadata, and summaries
    v
Baseline comparison
    |
    | JSON report and process exit code
    v
Local review or automation
```

The all-reduce adapter is an additional integration. The standalone benchmark
does not require the collective-communication repository.

## Requirements

- Linux.
- Python 3.12 or later.
- A C++17 compiler.
- CMake 3.20 or later.
- Ninja for the build commands below.

The Python implementation uses only the standard library.

## Build and test

From the repository root:

```bash
cmake -S . -B build/release -G Ninja \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build/release

PYTHONPATH=src python3 -m unittest discover -s tests -v

ctest --test-dir build/release --output-on-failure
```

The Python suite contains 35 test methods. The CTest entry runs four
compiled-benchmark integration test methods, including multiple payload
sizes and invalid arguments.

Tests cover subprocess handling, timeout cleanup, output validation,
statistics, experiment preservation, comparison decisions, and CLI exit codes.

## Run the standalone benchmark directly

```bash
./build/release/tcp_roundtrip 64 5 10
```

Arguments are payload bytes, warmup iterations, and measured iterations.

The program emits one JSON record after all payload checks succeed.
Connection setup, payload preparation, and verification are outside
the timed interval.

## Record independent runs

Commit source changes before recording an experiment. The recorder requires
a clean Git working tree so metadata identifies the source revision.

```bash
PYTHONPATH=src python3 -m netperf.tcp \
  --output results/reference-v1 \
  --payload-bytes 64 \
  --warmup 10 \
  --iterations 100 \
  --runs 3 \
  --timeout-seconds 30
```

Repeat with a new output directory for a candidate experiment:

```bash
PYTHONPATH=src python3 -m netperf.tcp \
  --output results/candidate-v1 \
  --payload-bytes 64 \
  --warmup 10 \
  --iterations 100 \
  --runs 3 \
  --timeout-seconds 30
```

Each run launches a fresh process. Existing experiment directories are rejected.

An experiment contains:

```text
environment.json
run-001.stdout
run-001.stderr
run-001.execution.json
run-001.json
...
summary.json
```

The final summary is written only after every requested run passes validation.
Failed executions retain captured diagnostics when available.

## Compare experiments

```bash
PYTHONPATH=src python3 -m netperf.compare \
  --baseline results/reference-v1 \
  --candidate results/candidate-v1 \
  --relative-threshold-percent 20 \
  --absolute-threshold-us 5
```

The comparator reloads validated sample records, recomputes statistics,
and checks that the saved summaries match.

It requires at least three runs per experiment and matching payload size,
warmup count, measured iteration count, run count, and recorded measurement
environment fields.

For each run:

- Median is calculated from its measured samples.
- p95 uses the nearest-rank definition.

Across runs, the comparator evaluates:

- Median of run medians.
- Median of run p95 values.

The second metric is not a pooled p95 across all samples.

A metric flags a regression only when its slowdown strictly exceeds both
the percentage threshold and the absolute threshold.

| Exit code | Meaning |
|---|---|
| 0 | No metric exceeded both thresholds |
| 2 | At least one metric exceeded both thresholds |
| 1 | Invalid, incompatible, or incomplete input |

The example thresholds are demonstration settings, not calibrated limits.

## Published example evidence

Two local experiments and their comparison are stored under:

```text
docs/experiments/tcp-smoke-v1/
docs/experiments/tcp-candidate-v1/
docs/experiments/smoke-comparison.json
```

Reproduce the comparison:

```bash
PYTHONPATH=src python3 -m netperf.compare \
  --baseline docs/experiments/tcp-smoke-v1 \
  --candidate docs/experiments/tcp-candidate-v1 \
  --relative-threshold-percent 20 \
  --absolute-threshold-us 5
```

Both experiments used the unchanged TCP benchmark implementation.

| Metric | Reference | Candidate |
|---|---:|---:|
| Median of run medians | 29.794 us | 28.616 us |
| Median of run p95 values | 40.631 us | 38.297 us |

These measurements demonstrate the recording and comparison workflow.
They do not establish a performance optimization.

## Optional all-reduce integration

With the separate collective-communication repository built:

```bash
PYTHONPATH=src python3 -m netperf.allreduce \
  --source ~/projects/collective-communication \
  --output results/allreduce-example-v1 \
  --ranks 2 \
  --elements 1024 \
  --warmup 5 \
  --iterations 10 \
  --runs 3 \
  --timeout-seconds 180
```

That adapter invokes the external launcher's Release-build and clean-tree
checks, then validates its summary CSV.

The TCP baseline comparator currently accepts standalone TCP experiment
directories. It does not compare all-reduce experiments.

## GitHub Actions

CI runs the Python tests, builds the C++ benchmark, runs integration tests,
and records two smoke experiments from the same build.

The comparison is informational on the shared runner. Invalid experiments
fail the workflow; timing threshold results are reported without failing CI.

Downloadable evidence includes experiment files, the comparison report,
the CMake cache, and available CTest diagnostics.

## Measurement scope and limitations

- The TCP benchmark uses IPv4 loopback, two sockets, and one echo thread
  within one process.
- Results include local scheduling and operating-system effects.
- No CPU affinity or machine-load control is applied.
- Measurements are round-trip latency, not physical network bandwidth.
- Threshold checks are not statistical significance tests.
- Matching recorded environment fields does not prove identical hardware
  or background load.
- The standalone recorder fingerprints the executable but does not verify
  that it was built from the recorded Git revision or enforce Release flags.
  Build the intended version before measuring.
- Worker socket timeouts bound individual blocking operations. The Python
  runner supplies the whole-command timeout.
- Linux timeout cleanup covers workers that remain in the process group.
- Command output is buffered in memory; use controlled benchmark programs
  with bounded output.