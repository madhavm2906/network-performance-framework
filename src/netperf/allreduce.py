"""Launch the existing all-reduce benchmark and validate its summary CSV.

This adapter checks configuration, command success, and summary structure.
The C++ benchmark remains responsible for numerical correctness checks.
"""

import argparse
import csv
import math
from pathlib import Path
import sys

from netperf.runner import run_command


FIELDS = [
    "ranks", "elements", "payload_bytes", "run", "samples",
    "median_us", "p95_us", "min_us", "max_us",
]


def validate_configuration(ranks, elements, warmup, iterations, runs):
    """Match worker limits; cap independent runs at 100 for this adapter."""
    for name, value, maximum in (
        ("ranks", ranks, 64),
        ("elements", elements, 1048576),
        ("warmup", warmup, 1000),
        ("iterations", iterations, 10000),
        ("runs", runs, 100),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")


def read_summary(path, *, ranks, elements, iterations, runs):
    """Require exactly one valid summary row for every requested run.

    Values are checked for consistency, not recomputed from raw samples yet.
    A well-formed summary alone does not prove numerical correctness.
    """
    records = []
    seen = set()

    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)

        if reader.fieldnames != FIELDS:
            raise ValueError("unexpected summary CSV columns")

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("summary row has missing or extra fields")

            record = {name: int(row[name]) for name in FIELDS[:5]}
            record.update({name: float(row[name]) for name in FIELDS[5:]})

            if (
                record["ranks"] != ranks
                or record["elements"] != elements
                or record["payload_bytes"] != elements * 8
                or record["samples"] != iterations
            ):
                raise ValueError(
                    "summary metadata does not match configuration"
                )

            run = record["run"]

            if not 1 <= run <= runs or run in seen:
                raise ValueError(
                    "summary contains a duplicate or unexpected run"
                )

            values = [record[name] for name in FIELDS[5:]]

            if any(not math.isfinite(value) or value <= 0 for value in values):
                raise ValueError(
                    "summary timings must be finite and positive"
                )

            if not (
                record["min_us"] <= record["median_us"]
                <= record["p95_us"] <= record["max_us"]
            ):
                raise ValueError(
                    "summary timing statistics are inconsistent"
                )

            seen.add(run)
            records.append(record)

    if seen != set(range(1, runs + 1)):
        raise ValueError("summary is missing requested runs")

    return sorted(records, key=lambda record: record["run"])


def run_allreduce(
    source,
    output,
    *,
    ranks=2,
    elements=1024,
    warmup=5,
    iterations=50,
    runs=3,
    timeout_seconds=180,
):
    """Run one configuration through project 1's launcher.

    source is the collective-communication repository. Output is resolved
    relative to the caller and must be new. The launcher checks the Release
    build, disabled sanitizers, and clean source tree, and saves raw evidence.
    """
    validate_configuration(ranks, elements, warmup, iterations, runs)

    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    launcher = source / "benchmarks" / "run_benchmarks.py"

    if not launcher.is_file():
        raise FileNotFoundError(
            f"benchmark launcher not found: {launcher}"
        )

    if output.exists():
        raise FileExistsError(
            f"preserve existing results; choose a new output: {output}"
        )

    command = [
        sys.executable,
        str(launcher),
        "--build-dir", "build/release",
        "--ranks", str(ranks),
        "--elements", str(elements),
        "--warmup", str(warmup),
        "--iterations", str(iterations),
        "--runs", str(runs),
        "--output", str(output),
    ]

    result = run_command(
        command,
        timeout_seconds=timeout_seconds,
        cwd=source,
    )

    if not result.succeeded:
        reason = (
            "timed out"
            if result.timed_out
            else f"exited with {result.returncode}"
        )
        raise RuntimeError(
            f"benchmark {reason}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    records = read_summary(
        output / "summary.csv",
        ranks=ranks,
        elements=elements,
        iterations=iterations,
        runs=runs,
    )

    return result, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)

    for name, default in (
        ("ranks", 2),
        ("elements", 1024),
        ("warmup", 5),
        ("iterations", 50),
        ("runs", 3),
    ):
        parser.add_argument(f"--{name}", type=int, default=default)

    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180,
    )

    args = parser.parse_args()

    try:
        result, records = run_allreduce(**vars(args))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"Accepted {len(records)} benchmark runs; "
        "summary validation passed."
    )

    for record in records:
        print(
            f"run={record['run']} "
            f"median={record['median_us']:.3f} us "
            f"p95={record['p95_us']:.3f} us"
        )

    print(
        f"Whole-command time: "
        f"{result.elapsed_seconds:.3f} seconds"
    )
    print(
        f"Results: "
        f"{Path(args.output).expanduser().resolve()}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())