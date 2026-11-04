"""Own and reap the two sandbox processes in a synthetic feasibility run."""

import contextlib
import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence

from scripts.feasibility_diagnostics import StartupDiagnostics
from scripts.feasibility_workspace import Workspace


class RunError(Exception):
    """The isolated run failed or did not finish within its deadline."""


def run_pair(
    workspace: Workspace,
    server_command: Sequence[str],
    client_command: Sequence[str],
    *,
    timeout: float,
    on_success: Callable[[], None] | None = None,
    diagnostics: StartupDiagnostics | None = None,
) -> None:
    """Run owned commands; preserve the workspace if their exit cannot be established.

    Callers are responsible for constructing fixed, sandboxed commands. The server must emit a
    single readiness line and exit after the client completes. SIGKILL or machine failure leaves
    the unsettled marker in place and deliberately requires manual recovery.
    """
    if timeout <= 0:
        raise RunError("timeout must be positive")
    deadline = time.monotonic() + timeout
    children: list[subprocess.Popen[bytes]] = []
    workspace.mark_launched()
    outcome = "launch_error"
    server = None
    try:
        if diagnostics is not None:
            diagnostics.mark("sandbox_launch")
        server = subprocess.Popen(  # noqa: S603  # Commands are constructed by the caller.
            list(server_command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        children.append(server)
        outcome = "readiness_error"
        assert server.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout, selectors.EVENT_READ)
            ready = bytearray()
            while not ready.endswith(b"\n") and len(ready) <= 128:
                if not selector.select(max(0, deadline - time.monotonic())):
                    outcome = "readiness_timeout"
                    raise RunError("server readiness timeout")
                chunk = os.read(server.stdout.fileno(), 128 - len(ready))
                if not chunk:
                    outcome = "premature_eof"
                    # EOF can precede waitpid visibility by a scheduler tick. Observe the
                    # natural status with a finite wait before cleanup sends any signals.
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        server.wait(timeout=min(0.1, max(0, deadline - time.monotonic())))
                    raise RunError("server exited before readiness")
                ready.extend(chunk)
                if len(ready) == 128 and not ready.endswith(b"\n"):
                    break
            if ready != b"SYNTHETIC_GATEWAY_READY\n":
                raise RunError("server did not report readiness")
        client = subprocess.Popen(  # noqa: S603  # Commands are constructed by the caller.
            list(client_command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        children.append(client)
        outcome = "client_error"
        try:
            if client.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                raise RunError("client failed")
            if server.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                outcome = "server_error"
                raise RunError("server failed")
            if on_success is not None:
                on_success()
            outcome = "success"
        except subprocess.TimeoutExpired as exc:
            outcome = "run_timeout"
            raise RunError("run timed out") from exc
    except (KeyboardInterrupt, SystemExit):
        outcome = "cancelled"
        raise
    finally:
        # Sample before cleanup signals; never attribute our TERM/KILL to the original failure.
        observed_returncode = server.poll() if server is not None else None
        settled = True
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        settled = False
            if child.stdout is not None:
                child.stdout.close()
        try:
            if diagnostics is not None:
                diagnostics.capture(observed_returncode, outcome, settled)
        finally:
            if settled:
                workspace.mark_settled()
                workspace.cleanup()
