import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/netpol_filter_cleanup.py"
spec = importlib.util.spec_from_file_location("netpol_cleanup", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cleanup_transaction = module.cleanup_transaction


def test_rehearsal_refuses_an_unrelated_context_before_mutation(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "netpol_filter_cleanup", module)
    rehearsal_spec = importlib.util.spec_from_file_location(
        "netpol_rehearsal", SCRIPT.parent / "rehearse_netpol_only.py"
    )
    assert rehearsal_spec and rehearsal_spec.loader
    rehearsal = importlib.util.module_from_spec(rehearsal_spec)
    rehearsal_spec.loader.exec_module(rehearsal)
    calls = []

    def command(args):
        calls.append(args)
        return '{"contexts":[{"context":{"cluster":"unrelated-cluster"}}]}'

    monkeypatch.setattr(rehearsal, "run", command)
    with pytest.raises(RuntimeError, match="Refusing a non-disposable cluster"):
        rehearsal.main()
    assert len(calls) == 1
    assert "config" in calls[0]


def test_scoped_transaction_preserves_unrelated_rules_and_quoted_comments() -> None:
    snapshot = """# snapshot
*filter
:INPUT ACCEPT [1:2]
:FORWARD ACCEPT [0:0]
:KUBE-SERVICES - [0:0]
:CNI-FORWARD - [0:0]
:KUBE-ROUTER-INPUT - [0:0]
:KUBE-POD-FW-ABC - [0:0]
-A INPUT -m comment --comment "policy entry" -j KUBE-ROUTER-INPUT
-A INPUT -m comment --comment "-j KUBE-ROUTER-INPUT" -j ACCEPT
-A INPUT -j KUBE-SERVICES
-A FORWARD -j CNI-FORWARD
-A KUBE-ROUTER-INPUT -j KUBE-POD-FW-ABC
-A KUBE-POD-FW-ABC -j RETURN
COMMIT
"""
    transaction = cleanup_transaction(snapshot)
    assert (
        transaction
        == """*filter
-D INPUT -m comment --comment "policy entry" -j KUBE-ROUTER-INPUT
-F KUBE-POD-FW-ABC
-F KUBE-ROUTER-INPUT
-X KUBE-POD-FW-ABC
-X KUBE-ROUTER-INPUT
COMMIT
"""
    )


def test_goto_detaches_from_unrelated_user_chain_without_flushing_it() -> None:
    snapshot = (
        "*filter\n:USER - [0:0]\n:KUBE-NWPLCY-COMMON - [0:0]\n"
        "-A USER -g KUBE-NWPLCY-COMMON\nCOMMIT\n"
    )
    transaction = cleanup_transaction(snapshot)
    assert transaction is not None
    assert "-D USER -g KUBE-NWPLCY-COMMON" in transaction
    assert "-F USER" not in transaction


def test_absent_reserved_chains_causes_no_restore_operation() -> None:
    assert cleanup_transaction("*filter\n:KUBE-SERVICES - [0:0]\nCOMMIT\n") is None


@pytest.mark.parametrize(
    "snapshot",
    [
        "*nat\nCOMMIT\n",
        "*filter\nCOMMIT\n*nat\nCOMMIT\n",
        "*filter\n:INPUT ACCEPT [0:0]\n",
        "*filter\n-F INPUT\nCOMMIT\n",
        '*filter\n-A INPUT --comment "unterminated\nCOMMIT\n',
        "*filter\n:INPUT ACCEPT\nCOMMIT\n",
        "*filter\n:KUBE-NWPLCY-DEFAULT - [0:0]\n-A INPUT -j\nCOMMIT\n",
    ],
)
def test_malformed_or_wrong_table_fails_closed(snapshot: str) -> None:
    with pytest.raises(ValueError):
        cleanup_transaction(snapshot)
