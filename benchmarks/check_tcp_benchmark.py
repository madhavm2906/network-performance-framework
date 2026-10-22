"""Exercise the compiled TCP worker.

CTest supplies the compiled executable's absolute path.
These checks are separate from the framework's Python unit tests.
"""

import json
import math
from pathlib import Path
import subprocess
import sys
import unittest


BINARY = str(Path(sys.argv.pop(1)).resolve())


def execute(*arguments):
    """Bound each test invocation so a broken worker cannot hang the suite."""
    return subprocess.run(
        [BINARY, *map(str, arguments)],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TcpBenchmarkTests(unittest.TestCase):
    def check_record(self, bytes_count, warmup, iterations):
        result = execute(bytes_count, warmup, iterations)

        self.assertEqual(
            result.returncode,
            0,
            result.stderr,
        )

        record = json.loads(result.stdout)

        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["benchmark"], "tcp_roundtrip")
        self.assertEqual(record["transport"], "ipv4_loopback")
        self.assertIs(record["verified"], True)
        self.assertEqual(record["payload_bytes"], bytes_count)
        self.assertEqual(record["warmup"], warmup)
        self.assertEqual(record["iterations"], iterations)

        samples = record["samples_us"]

        self.assertEqual(len(samples), iterations)

        for sample in samples:
            self.assertTrue(type(sample) in (int, float))
            self.assertTrue(
                math.isfinite(sample) and sample > 0
            )

    def test_payload_sizes(self):
        # Include a large payload that requires complete transfer loops.
        for size in (1, 64, 8192, 1048576):
            with self.subTest(size=size):
                self.check_record(size, 2, 5)

    def test_zero_warmup(self):
        self.check_record(64, 0, 1)

    def test_invalid_arguments(self):
        for args in (
            (0, 2, 5),
            (1048577, 2, 5),
            (64, 1001, 5),
            (64, 2, 0),
            (64, 2, 10001),
            ("-1", 2, 5),
            ("abc", 2, 5),
            ("1.5", 2, 5),
        ):
            with self.subTest(args=args):
                result = execute(*args)

                self.assertNotEqual(
                    result.returncode,
                    0,
                )
                self.assertEqual(result.stdout, "")
                self.assertIn("ERROR:", result.stderr)

    def test_missing_arguments(self):
        result = execute()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)