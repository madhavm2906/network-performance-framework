"""Run commands without a shell and bound their execution time.

On Linux, each command gets a process group so timeout cleanup also
stops workers spawned by the benchmark launcher.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
import subprocess
import time


@dataclass(frozen=True)
class RunResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def run_command(
    command: list[str],
    *,
    timeout_seconds: float,
    cwd: str | Path | None = None,
) -> RunResult:
    """Capture one command; invalid input and launch errors raise exceptions.

    A nonzero exit or timeout is a recorded result, not a launch error.
    elapsed_seconds measures the whole command, including startup and cleanup;
    it is not the benchmark's own latency measurement.
    """
    if isinstance(command, (str, bytes)) or not command:
        raise ValueError("command must be a nonempty list of arguments")
    if any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise ValueError("arguments must be strings without null bytes")
    if not command[0]:
        raise ValueError("executable must not be empty")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")

    arguments = tuple(command)
    started = time.perf_counter()
    process = subprocess.Popen(
        arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        start_new_session=(os.name == "posix"),
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The group exited between the timeout and cleanup.
        else:
            process.kill()
        stdout, stderr = process.communicate()

    return RunResult(
        command=arguments,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=time.perf_counter() - started,
        timed_out=timed_out,
    )
