"""Compare validated TCP experiments using configurable slowdown thresholds.

This is a deterministic threshold check, not a statistical significance test.
Median latency and tail latency are evaluated separately across repeated runs.
"""

import argparse
import json
import math
from pathlib import Path
import statistics
import sys

from netperf.tcp import validate_record


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_experiment(directory):
    """Recompute statistics from raw records and verify the saved summary."""
    directory = Path(directory).expanduser().resolve()

    environment = read_json(directory / "environment.json")
    saved = read_json(directory / "summary.json")

    if not isinstance(environment, dict) or not isinstance(saved, dict):
        raise ValueError(
            "environment and summary must be JSON objects"
        )

    config = environment.get("configuration")

    if not isinstance(config, dict):
        raise ValueError("missing experiment configuration")

    for name, minimum, maximum in (
        ("payload_bytes", 1, 1048576),
        ("warmup", 0, 1000),
        ("iterations", 1, 10000),
        ("runs", 3, 100),
    ):
        value = config.get(name)

        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(
                f"invalid {name}; comparison requires "
                "at least three runs"
            )

    for name in ("platform", "measurement", "scope"):
        if (
            not isinstance(environment.get(name), str)
            or not environment[name]
        ):
            raise ValueError(
                f"missing environment field: {name}"
            )

    if environment.get("percentile_definition") != "nearest-rank":
        raise ValueError("unsupported percentile definition")

    rows = []

    for run in range(1, config["runs"] + 1):
        record = read_json(
            directory / f"run-{run:03d}.json"
        )

        row = validate_record(
            record,
            payload_bytes=config["payload_bytes"],
            warmup=config["warmup"],
            iterations=config["iterations"],
        )

        row["run"] = run
        rows.append(row)

    median = statistics.median(
        row["median_us"] for row in rows
    )

    expected = {
        "schema_version": 1,
        "benchmark": "tcp_roundtrip",
        "payload_bytes": config["payload_bytes"],
        "runs": rows,
        "median_of_run_medians_us": median,
    }

    if saved != expected:
        raise ValueError(
            "saved summary does not match validated raw records"
        )

    return {
        "directory": str(directory),
        "configuration": config,
        "environment": environment,
        "metrics": {
            "median_of_run_medians_us": median,
            "median_of_run_p95s_us": statistics.median(
                row["p95_us"] for row in rows
            ),
        },
        "run_medians_us": [
            row["median_us"] for row in rows
        ],
        "run_p95s_us": [
            row["p95_us"] for row in rows
        ],
    }


def compare_experiments(
    baseline,
    candidate,
    *,
    relative_threshold_percent=10,
    absolute_threshold_us=2,
):
    for value in (
        relative_threshold_percent,
        absolute_threshold_us,
    ):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(
                "thresholds must be finite nonnegative numbers"
            )

    baseline = load_experiment(baseline)
    candidate = load_experiment(candidate)

    if baseline["directory"] == candidate["directory"]:
        raise ValueError(
            "baseline and candidate must be separate experiments"
        )

    for name in (
        "payload_bytes",
        "warmup",
        "iterations",
        "runs",
    ):
        if (
            baseline["configuration"][name]
            != candidate["configuration"][name]
        ):
            raise ValueError(
                f"incompatible experiment setting: {name}"
            )

    for name in (
        "platform",
        "measurement",
        "scope",
        "percentile_definition",
    ):
        if (
            baseline["environment"][name]
            != candidate["environment"][name]
        ):
            raise ValueError(
                f"incompatible recorded environment: {name}"
            )

    metrics = {}

    for name, original in baseline["metrics"].items():
        current = candidate["metrics"][name]
        delta = current - original
        percent = delta / original * 100

        metrics[name] = {
            "baseline_us": original,
            "candidate_us": current,
            "delta_us": delta,
            "change_percent": percent,
            "regression": (
                percent > relative_threshold_percent
                and delta > absolute_threshold_us
            ),
        }

    regression = any(
        metric["regression"]
        for metric in metrics.values()
    )

    return {
        "schema_version": 1,
        "status": "regression" if regression else "passed",
        "baseline": baseline["directory"],
        "candidate": candidate["directory"],
        "configuration": baseline["configuration"],
        "thresholds": {
            "relative_percent": relative_threshold_percent,
            "absolute_us": absolute_threshold_us,
            "rule": (
                "A slowdown must strictly exceed both thresholds."
            ),
        },
        "metrics": metrics,
        "independent_runs": {
            "baseline_medians_us": baseline["run_medians_us"],
            "candidate_medians_us": candidate["run_medians_us"],
            "baseline_p95s_us": baseline["run_p95s_us"],
            "candidate_p95s_us": candidate["run_p95s_us"],
        },
        "interpretation": (
            "Threshold check only; this does not establish "
            "statistical significance or prove that a code "
            "change caused a slowdown. Matching recorded "
            "environment fields does not establish identical "
            "hardware or machine load."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)

    parser.add_argument(
        "--relative-threshold-percent",
        type=float,
        default=10,
    )
    parser.add_argument(
        "--absolute-threshold-us",
        type=float,
        default=2,
    )

    args = parser.parse_args()

    try:
        report = compare_experiments(**vars(args))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            report,
            indent=2,
            allow_nan=False,
        )
    )

    return 2 if report["status"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())