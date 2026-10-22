"""Check comparison decisions using controlled experiments, not real timing."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from netperf.compare import compare_experiments, main
from netperf.tcp import validate_record


class CompareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        self.root = Path(directory.name)
        self.baseline = self.make_experiment(
            "baseline",
            100,
        )

    def make_experiment(self, name, latency):
        directory = self.root / name
        directory.mkdir()

        environment = {
            "configuration": {
                "payload_bytes": 64,
                "warmup": 10,
                "iterations": 4,
                "runs": 3,
            },
            "platform": "test-machine",
            "measurement": "test round trip",
            "scope": "test",
            "percentile_definition": "nearest-rank",
        }

        (directory / "environment.json").write_text(
            json.dumps(environment)
        )

        rows = []

        for run in range(1, 4):
            record = {
                "schema_version": 1,
                "benchmark": "tcp_roundtrip",
                "transport": "ipv4_loopback",
                "verified": True,
                "payload_bytes": 64,
                "warmup": 10,
                "iterations": 4,
                "samples_us": [latency] * 4,
            }

            (directory / f"run-{run:03d}.json").write_text(
                json.dumps(record)
            )

            row = validate_record(
                record,
                payload_bytes=64,
                warmup=10,
                iterations=4,
            )

            row["run"] = run
            rows.append(row)

        summary = {
            "schema_version": 1,
            "benchmark": "tcp_roundtrip",
            "payload_bytes": 64,
            "runs": rows,
            "median_of_run_medians_us": latency,
        }

        (directory / "summary.json").write_text(
            json.dumps(summary)
        )

        return directory

    def test_equal_and_faster_candidates_pass(self):
        for latency in (100, 80):
            candidate = self.make_experiment(
                f"candidate-{latency}",
                latency,
            )

            report = compare_experiments(
                self.baseline,
                candidate,
            )

            self.assertEqual(report["status"], "passed")

    def test_slowdown_exceeding_both_thresholds_fails(self):
        candidate = self.make_experiment("slow", 120)

        report = compare_experiments(
            self.baseline,
            candidate,
        )

        self.assertEqual(report["status"], "regression")

        metric = report["metrics"]["median_of_run_medians_us"]

        self.assertAlmostEqual(
            metric["change_percent"],
            20,
        )

    def test_exceeding_only_one_threshold_passes(self):
        candidate = self.make_experiment("candidate", 120)

        for options in (
            {"relative_threshold_percent": 30},
            {"absolute_threshold_us": 30},
        ):
            report = compare_experiments(
                self.baseline,
                candidate,
                **options,
            )

            self.assertEqual(report["status"], "passed")

    def test_tail_regression_is_detected_with_unchanged_median(self):
        candidate = self.make_experiment("tail", 100)

        summary_path = candidate / "summary.json"
        summary = json.loads(summary_path.read_text())

        for run in range(1, 4):
            path = candidate / f"run-{run:03d}.json"
            record = json.loads(path.read_text())

            record["samples_us"] = [100, 100, 100, 200]
            path.write_text(json.dumps(record))

            summary["runs"][run - 1].update({
                "p95_us": 200,
                "max_us": 200,
            })

        summary_path.write_text(json.dumps(summary))

        report = compare_experiments(
            self.baseline,
            candidate,
        )

        self.assertEqual(report["status"], "regression")
        self.assertFalse(
            report["metrics"]
            ["median_of_run_medians_us"]
            ["regression"]
        )
        self.assertTrue(
            report["metrics"]
            ["median_of_run_p95s_us"]
            ["regression"]
        )

    def test_exact_threshold_is_not_exceeded(self):
        candidate = self.make_experiment("candidate", 110)

        self.assertEqual(
            compare_experiments(
                self.baseline,
                candidate,
            )["status"],
            "passed",
        )

    def test_modified_summary_is_rejected(self):
        candidate = self.make_experiment("candidate", 100)

        path = candidate / "summary.json"
        summary = json.loads(path.read_text())

        summary["median_of_run_medians_us"] = 1
        path.write_text(json.dumps(summary))

        with self.assertRaises(ValueError):
            compare_experiments(
                self.baseline,
                candidate,
            )

    def test_incompatible_configuration_is_rejected(self):
        candidate = self.make_experiment("candidate", 100)

        path = candidate / "environment.json"
        environment = json.loads(path.read_text())

        environment["configuration"]["warmup"] = 11
        path.write_text(json.dumps(environment))

        with self.assertRaises(ValueError):
            compare_experiments(
                self.baseline,
                candidate,
            )

    def test_missing_raw_record_is_rejected(self):
        candidate = self.make_experiment("candidate", 100)

        (candidate / "run-002.json").unlink()

        with self.assertRaises(FileNotFoundError):
            compare_experiments(
                self.baseline,
                candidate,
            )

    def test_invalid_thresholds_are_rejected(self):
        for value in (
            -1,
            float("nan"),
            float("inf"),
            True,
        ):
            with self.assertRaises(ValueError):
                compare_experiments(
                    self.baseline,
                    self.baseline,
                    relative_threshold_percent=value,
                )

    def test_cli_exit_codes(self):
        candidate = self.make_experiment("slow", 120)

        for target, expected in (
            (self.baseline, 0),
            (candidate, 2),
        ):
            baseline = self.make_experiment(
                f"reference-{expected}",
                100,
            )

            with patch(
                "sys.argv",
                [
                    "compare",
                    "--baseline",
                    str(baseline),
                    "--candidate",
                    str(target),
                ],
            ), patch("builtins.print"):
                self.assertEqual(main(), expected)

        with patch(
            "sys.argv",
            [
                "compare",
                "--baseline",
                str(self.root / "missing"),
                "--candidate",
                str(candidate),
            ],
        ), patch("builtins.print"):
            self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)