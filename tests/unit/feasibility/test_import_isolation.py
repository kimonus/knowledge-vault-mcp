"""The spike must not replace the integration suite's root conftest module."""

import subprocess
import sys
from pathlib import Path


def test_root_conftest_remains_importable() -> None:
    import conftest

    root_conftest = Path(__file__).resolve().parents[3] / "tests" / "conftest.py"
    assert Path(conftest.__file__).resolve() == root_conftest
    assert conftest.POSTGRES_IMAGE.startswith("pgvector/pgvector:")


def test_import_restores_python_path(tmp_path: Path) -> None:
    unit_dir = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "before = sys.path.copy(); import feasibility; "
        "assert sys.path == before"
    )
    subprocess.run(  # noqa: S603  # Fixed interpreter and test-only code.
        [sys.executable, "-c", code, str(unit_dir)], cwd=tmp_path, check=True, timeout=10
    )
