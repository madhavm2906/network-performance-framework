"""Check summary rejection and adapter behavior without the C++ repository."""

import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from netperf.allreduce import FIELDS, read_summary, run_allreduce
from netperf.runner import RunResult


class AllreduceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        self.root = Path(self.directory.name)
        self.summary = self.root / "summary.csv"

        # ranks, elements, bytes, run, samples, median, p95, min, max
        self.row = [2, 1024, 8192, 1, 10, 20, 30, 10, 40]

    def write_summary(self, rows, fields=FIELDS):
        with self.summary.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)

    def read(self, runs=1):
        return read_summary(
            self.summary,
            ranks=2,
            elements=1024,
            iterations=10,
            runs=runs,
        )

    def test_valid_summary(self):
        self.write_summary([self.row])

        self.assertEqual(
            self.read()[0]["median_us"],
            20,
        )

    def test_rejects_wrong_columns(self):
        self.write_summary([], fields=["latency"])

        with self.assertRaises(ValueError):
            self.read()

    def test_rejects_missing_duplicate_and_unexpected_runs(self):
        unexpected = self.row.copy()
        unexpected[3] = 2

        for rows in (
            [],
            [self.row, self.row],
            [unexpected],
        ):
            with self.subTest(rows=rows):
                self.write_summary(rows)

                with self.assertRaises(ValueError):
                    self.read()

    def test_rejects_mismatched_metadata(self):
        for index in (0, 1, 2, 4):
            row = self.row.copy()
            row[index] += 1

            self.write_summary([row])

            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.read()

    def test_rejects_invalid_timings(self):
        for value in (
            "nan",
            "inf",
            "-inf",
            "oops",
            0,
            -1,
            50,
        ):
            row = self.row.copy()
            row[5] = value

            self.write_summary([row])

            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.read()

    def test_rejects_ragged_rows(self):
        for row in (
            self.row[:-1],
            self.row + [99],
        ):
            self.write_summary([row])

            with self.assertRaises(ValueError):
                self.read()

    def make_source(self):
        launcher = (
            self.root
            / "source"
            / "benchmarks"
            / "run_benchmarks.py"
        )
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "# Mocked launcher\n",
            encoding="utf-8",
        )

        return launcher.parents[1]

    def test_success_builds_arguments_and_validates_output(self):
        source = self.make_source()
        output = self.root / "experiment"

        def fake_run(command, **kwargs):
            self.assertEqual(
                Path(kwargs["cwd"]),
                source.resolve(),
            )

            for option, expected in (
                ("--ranks", "2"),
                ("--elements", "1024"),
                ("--warmup", "5"),
                ("--iterations", "10"),
                ("--runs", "1"),
                ("--build-dir", "build/release"),
                ("--output", str(output.resolve())),
            ):
                self.assertEqual(
                    command[command.index(option) + 1],
                    expected,
                )

            output.mkdir()
            self.write_summary([self.row])
            self.summary.rename(output / "summary.csv")

            return RunResult(
                tuple(command),
                0,
                "",
                "",
                0.1,
                False,
            )

        with patch(
            "netperf.allreduce.run_command",
            side_effect=fake_run,
        ):
            _, records = run_allreduce(
                source,
                output,
                iterations=10,
                runs=1,
            )

        self.assertEqual(len(records), 1)

    def test_failed_or_timed_out_command_is_rejected(self):
        source = self.make_source()

        for timed_out in (False, True):
            result = RunResult(
                (),
                7,
                "partial",
                "failure",
                0.1,
                timed_out,
            )

            with patch(
                "netperf.allreduce.run_command",
                return_value=result,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "partial",
                ):
                    run_allreduce(
                        source,
                        self.root / "new-output",
                    )

    def test_preserves_existing_output(self):
        source = self.make_source()

        with patch(
            "netperf.allreduce.run_command",
        ) as execute:
            with self.assertRaises(FileExistsError):
                run_allreduce(source, self.root)

            execute.assert_not_called()

    def test_invalid_configuration_never_launches(self):
        with patch(
            "netperf.allreduce.run_command",
        ) as execute:
            for options in (
                {"ranks": 65},
                {"iterations": 0},
                {"runs": True},
            ):
                with self.assertRaises(ValueError):
                    run_allreduce(
                        self.root,
                        self.root / "new",
                        **options,
                    )

            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)