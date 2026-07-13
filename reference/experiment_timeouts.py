"""Canonical wall-clock limits for citable performance measurements.

The two limits apply to one timed cell, not to an entire multi-run driver:

* QUERY_TIMEOUT_S caps one SELECT or circuit CONSTRUCT execution, including the
  final response read.
* COMPILE_TIMEOUT_S caps one OBDD or d4/d-DNNF compilation attempt.

Short correctness probes may use a smaller network timeout, and dataset loading
may use a larger operational timeout. Neither is a reported performance cell.
Keep the paper-task protocol and every performance harness tied to these values.
"""

QUERY_TIMEOUT_S = 300
COMPILE_TIMEOUT_S = 120


class CompilationTimeout(TimeoutError):
    """One compilation attempt exceeded the canonical wall-clock limit."""


def compilation_timeout(seconds=COMPILE_TIMEOUT_S):
    """Return a POSIX hard-deadline context for an in-process compiler.

    The performance harnesses run compilation in the main thread of either the
    driver or a killable worker. Refuse a non-main-thread use rather than claim
    a timeout that cannot actually interrupt the compiler.
    """
    import contextlib
    import signal
    import threading
    import time

    @contextlib.contextmanager
    def deadline():
        if seconds <= 0:
            raise ValueError("compilation timeout must be positive")
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
            raise RuntimeError("hard compilation timeout requires a POSIX main thread or worker process")

        def expired(_signum, _frame):
            raise CompilationTimeout(f"compilation timed out after {seconds}s")

        old_handler = signal.getsignal(signal.SIGALRM)
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0] > 0:
                remaining = max(1e-6, old_timer[0] - (time.monotonic() - started))
                signal.setitimer(signal.ITIMER_REAL, remaining, old_timer[1])

    return deadline()
