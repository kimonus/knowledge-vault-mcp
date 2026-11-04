"""Own and reap the two sandbox processes in a synthetic feasibility run."""

import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence

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
    try:
        server = subprocess.Popen(  # noqa: S603  # Commands are constructed by the caller.
            list(server_command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        children.append(server)
        assert server.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout, selectors.EVENT_READ)
            ready = bytearray()
            while not ready.endswith(b"\n") and len(ready) <= 128:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise RunError("server readiness timeout")
                chunk = os.read(server.stdout.fileno(), 128 - len(ready))
                if not chunk:
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
        try:
            if client.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                raise RunError("client failed")
            if server.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                raise RunError("server failed")
            if on_success is not None:
                on_success()
        except subprocess.TimeoutExpired as exc:
            raise RunError("run timed out") from exc
    finally:
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
        if settled:
            workspace.mark_settled()
            workspace.cleanup()
