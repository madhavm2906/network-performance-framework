"""Validate statistics and experiment failure handling using controlled outputs."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from netperf.runner import RunResult
from netperf.tcp import run_experiment, validate_record


def record():
    """Return a known worker record with deliberately unsorted samples."""
    return {
        "schema_version": 1,
        "benchmark": "tcp_roundtrip",
        "transport": "ipv4_loopback",
        "verified": True,
        "payload_bytes": 64,
        "warmup": 5,
        "iterations": 4,
        "samples_us": [40, 10, 30, 20],
    }


def validate(value):
    return validate_record(
        value,
        payload_bytes=64,
        warmup=5,
        iterations=4,
    )


class TcpValidationTests(unittest.TestCase):
    def test_statistics_from_unsorted_samples(self):
        self.assertEqual(
            validate(record()),
            {
                "samples": 4,
                "median_us": 25,
                "p95_us": 40,
                "min_us": 10,
                "max_us": 40,
            },
        )

    def test_rejects_invalid_samples(self):
        for sample in (
            True,
            "12",
            0,
            -1,
            float("nan"),
            float("inf"),
            10 ** 400,
        ):
            value = record()
            value["samples_us"][0] = sample

            with self.subTest(sample=sample):
                with self.assertRaises(ValueError):
                    validate(value)

    def test_rejects_wrong_metadata(self):
        for key, replacement in (
            ("schema_version", True),
            ("payload_bytes", 65),
            ("warmup", 0),
            ("iterations", 3),
            ("verified", 1),
            ("benchmark", "other"),
            ("transport", "remote"),
            ("samples_us", [1, 2]),
        ):
            value = record()
            value[key] = replacement

            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate(value)

        with self.assertRaises(ValueError):
            validate([])


class TcpExperimentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        self.root = Path(directory.name)
        self.binary = self.root / "worker"
        self.binary.write_bytes(b"test executable placeholder")
        self.output = self.root / "experiment"

        # Unit tests do not need a real executable or Git repository.
        provenance_patch = patch(
            "netperf.tcp.provenance",
            return_value={"test": True},
        )
        provenance_patch.start()
        self.addCleanup(provenance_patch.stop)

    def execute(self, result, runs=1):
        with patch(
            "netperf.tcp.run_command",
            return_value=result,
        ):
            return run_experiment(
                self.binary,
                self.output,
                iterations=4,
                runs=runs,
            )

    def test_writes_validated_runs_and_summary(self):
        result = RunResult(
            (str(self.binary),),
            0,
            json.dumps(record()),
            "",
            0.1,
            False,
        )

        summary = self.execute(result, runs=2)

        self.assertEqual(len(summary["runs"]), 2)
        self.assertEqual(
            summary["median_of_run_medians_us"],
            25,
        )

        self.assertTrue(
            (self.output / "environment.json").is_file()
        )
        self.assertTrue(
            (self.output / "run-002.json").is_file()
        )

        saved = json.loads(
            (self.output / "summary.json").read_text()
        )
        self.assertEqual(saved, summary)

    def test_worker_failure_preserves_diagnostics_without_summary(self):
        result = RunResult(
            (),
            7,
            "partial",
            "worker failed",
            0.1,
            False,
        )

        with self.assertRaises(RuntimeError):
            self.execute(result)

        self.assertEqual(
            (self.output / "run-001.stderr").read_text(),
            "worker failed",
        )
        self.assertFalse(
            (self.output / "summary.json").exists()
        )

    def test_invalid_json_never_creates_summary(self):
        result = RunResult(
            (),
            0,
            "not JSON",
            "",
            0.1,
            False,
        )

        with self.assertRaises(ValueError):
            self.execute(result)

        self.assertTrue(
            (self.output / "run-001.stdout").exists()
        )
        self.assertFalse(
            (self.output / "summary.json").exists()
        )

    def test_existing_output_is_preserved(self):
        self.output.mkdir()

        marker = self.output / "keep.txt"
        marker.write_text("original")

        with self.assertRaises(FileExistsError):
            self.execute(
                RunResult((), 0, "", "", 0.1, False)
            )

        self.assertEqual(
            marker.read_text(),
            "original",
        )

    def test_invalid_configuration_is_rejected(self):
        for options in (
            {"runs": True},
            {"warmup": -1},
            {"timeout_seconds": 0},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    run_experiment(
                        self.binary,
                        self.output,
                        **options,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)