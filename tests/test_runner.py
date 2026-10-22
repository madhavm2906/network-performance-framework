"""Exercise real subprocess success, failure, arguments, and timeout cleanup."""

import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from netperf.runner import run_command


class RunnerTests(unittest.TestCase):
    def test_captures_both_streams(self):
        result = run_command(
            [sys.executable, "-c",
             "import sys; print('sample'); print('diagnostic', file=sys.stderr)"],
            timeout_seconds=5,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout.strip(), "sample")
        self.assertEqual(result.stderr.strip(), "diagnostic")
        self.assertGreater(result.elapsed_seconds, 0)

    def test_records_nonzero_exit(self):
        result = run_command(
            [sys.executable, "-c", "raise SystemExit(7)"],
            timeout_seconds=5,
        )
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.timed_out)

    def test_preserves_literal_arguments_and_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            argument = "spaces ; $HOME $(echo unexpected)"
            result = run_command(
                [sys.executable, "-c",
                 "import os, sys; print(os.getcwd()); print(sys.argv[1])",
                 argument],
                timeout_seconds=5,
                cwd=directory,
            )
            self.assertTrue(result.succeeded)
            lines = result.stdout.splitlines()
            self.assertEqual(Path(lines[0]).resolve(), Path(directory).resolve())
            self.assertEqual(lines[1], argument)

    def test_timeout_preserves_partial_output(self):
        result = run_command(
            [sys.executable, "-c",
             "import time; print('started', flush=True); time.sleep(30)"],
            timeout_seconds=1,
        )
        self.assertTrue(result.timed_out)
        self.assertFalse(result.succeeded)
        self.assertIn("started", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(os.name == "posix", "Linux process-group cleanup")
    def test_timeout_stops_child_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "worker-finished"
            worker = (
                "import pathlib, sys, time; time.sleep(3); "
                "pathlib.Path(sys.argv[1]).write_text('unexpected')"
            )
            launcher = (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                "print('worker launched', flush=True); time.sleep(30)"
            )
            result = run_command(
                [sys.executable, "-c", launcher, worker, str(marker)],
                timeout_seconds=1,
            )
            self.assertTrue(result.timed_out)
            self.assertIn("worker launched", result.stdout)
            time.sleep(3)
            self.assertFalse(marker.exists(), "worker survived the timeout")

    def test_rejects_invalid_configuration(self):
        for command in ([], "echo sample", [""], ["echo", "bad\0arg"]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                run_command(command, timeout_seconds=1)
        for timeout in (0, -1, math.inf, math.nan):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                run_command([sys.executable], timeout_seconds=timeout)

    def test_missing_executable_is_launch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                run_command(
                    [str(Path(directory) / "missing-executable")],
                    timeout_seconds=1,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
