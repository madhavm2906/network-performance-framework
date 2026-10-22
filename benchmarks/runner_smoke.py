"""Demonstrate command capture before connecting a real network benchmark."""

from dataclasses import asdict
import json
import sys

from netperf.runner import run_command


def main():
    result = run_command(
        [sys.executable, "-c", "print('runner smoke check passed')"],
        timeout_seconds=5,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
