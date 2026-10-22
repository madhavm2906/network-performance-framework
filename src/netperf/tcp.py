"""Run and record independent experiments using this repository's TCP worker."""

import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys

from netperf.runner import run_command


ROOT = Path(__file__).resolve().parents[2]


def validate_record(record, *, payload_bytes, warmup, iterations):
    """Reject incorrect metadata, unverified output, and invalid samples."""
    if not isinstance(record, dict):
        raise ValueError("worker output must be a JSON object")

    for name, expected in (
        ("schema_version", 1),
        ("payload_bytes", payload_bytes),
        ("warmup", warmup),
        ("iterations", iterations),
    ):
        if type(record.get(name)) is not int or record[name] != expected:
            raise ValueError(f"worker metadata mismatch: {name}")

    if (
        record.get("benchmark") != "tcp_roundtrip"
        or record.get("transport") != "ipv4_loopback"
        or record.get("verified") is not True
    ):
        raise ValueError(
            "unexpected benchmark, transport, or verification status"
        )

    samples = record.get("samples_us")

    if not isinstance(samples, list) or len(samples) != iterations:
        raise ValueError("incorrect sample count")

    for value in samples:
        # bool is deliberately rejected even though it subclasses int.
        if type(value) not in (int, float):
            raise ValueError("samples must be numeric")

        try:
            valid = math.isfinite(value) and value > 0
        except OverflowError:
            valid = False

        if not valid:
            raise ValueError("samples must be finite and positive")

    ordered = sorted(samples)

    return {
        "samples": len(samples),
        "median_us": statistics.median(ordered),
        "p95_us": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "min_us": ordered[0],
        "max_us": ordered[-1],
    }


def provenance(binary):
    """Require committed source and record the exact executable fingerprint."""
    def git(*arguments):
        result = run_command(
            ["git", *arguments],
            timeout_seconds=10,
            cwd=ROOT,
        )

        if not result.succeeded:
            raise RuntimeError(
                f"cannot read Git state: {result.stderr}"
            )

        return result.stdout.strip()

    if git("status", "--porcelain"):
        raise ValueError(
            "commit source changes before recording an experiment"
        )

    return {
        "git_revision": git("rev-parse", "HEAD"),
        "working_tree_clean": True,
        "binary_path": str(binary),
        "binary_sha256": hashlib.sha256(
            binary.read_bytes()
        ).hexdigest(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def write_json(path, value):
    path.write_text(
        json.dumps(
            value,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def run_experiment(
    binary,
    output,
    *,
    payload_bytes=64,
    warmup=5,
    iterations=50,
    runs=3,
    timeout_seconds=30,
):
    for name, value, minimum, maximum in (
        ("payload_bytes", payload_bytes, 1, 1048576),
        ("warmup", warmup, 0, 1000),
        ("iterations", iterations, 1, 10000),
        ("runs", runs, 1, 100),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(
                f"{name} must be an integer "
                f"from {minimum} to {maximum}"
            )

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout must be finite and positive")

    binary = Path(binary).expanduser().resolve()
    output = Path(output).expanduser().resolve()

    if not binary.is_file():
        raise FileNotFoundError(
            f"benchmark executable not found: {binary}"
        )

    if output.exists():
        raise FileExistsError(
            f"choose a new experiment directory: {output}"
        )

    environment = provenance(binary)

    environment.update({
        "created_utc": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "configuration": {
            "payload_bytes": payload_bytes,
            "warmup": warmup,
            "iterations": iterations,
            "runs": runs,
            "timeout_seconds": timeout_seconds,
        },
        "measurement": (
            "Local TCP request/echo round-trip latency "
            "in microseconds."
        ),
        "percentile_definition": "nearest-rank",
        "scope": (
            "Two sockets and one echo thread in one process; "
            "no CPU pinning."
        ),
    })

    # Never overwrite an existing experiment.
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "environment.json", environment)

    summaries = []

    for run in range(1, runs + 1):
        command = [
            str(binary),
            str(payload_bytes),
            str(warmup),
            str(iterations),
        ]

        result = run_command(
            command,
            timeout_seconds=timeout_seconds,
            cwd=ROOT,
        )

        prefix = output / f"run-{run:03d}"

        # Preserve diagnostics before checking success or parsing JSON.
        prefix.with_suffix(".stdout").write_text(
            result.stdout,
            encoding="utf-8",
        )
        prefix.with_suffix(".stderr").write_text(
            result.stderr,
            encoding="utf-8",
        )

        write_json(
            prefix.with_suffix(".execution.json"),
            {
                "command": list(result.command),
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "elapsed_seconds": result.elapsed_seconds,
            },
        )

        if not result.succeeded:
            raise RuntimeError(
                f"run {run} failed or timed out; "
                f"inspect {output}"
            )

        record = json.loads(result.stdout)

        summary = validate_record(
            record,
            payload_bytes=payload_bytes,
            warmup=warmup,
            iterations=iterations,
        )

        summary["run"] = run
        summaries.append(summary)

        write_json(
            prefix.with_suffix(".json"),
            record,
        )

    # Create the final summary only after every requested run passes.
    summary = {
        "schema_version": 1,
        "benchmark": "tcp_roundtrip",
        "payload_bytes": payload_bytes,
        "runs": summaries,
        "median_of_run_medians_us": statistics.median(
            row["median_us"] for row in summaries
        ),
    }

    write_json(output / "summary.json", summary)

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--binary",
        default=str(ROOT / "build/release/tcp_roundtrip"),
    )
    parser.add_argument("--output", required=True)

    for name, default in (
        ("payload-bytes", 64),
        ("warmup", 5),
        ("iterations", 50),
        ("runs", 3),
    ):
        parser.add_argument(
            f"--{name}",
            type=int,
            default=default,
        )

    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30,
    )

    args = parser.parse_args()

    try:
        summary = run_experiment(**vars(args))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    for row in summary["runs"]:
        print(
            f"run={row['run']} "
            f"median={row['median_us']:.3f} us "
            f"p95={row['p95_us']:.3f} us"
        )

    print(
        f"Accepted {len(summary['runs'])} runs. "
        f"Results: {Path(args.output).resolve()}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())