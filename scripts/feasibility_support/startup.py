"""Catch startup failures without exposing their messages, reprs, paths or tracebacks."""

import runpy

from scripts.feasibility_diagnostics import SANDBOX_PATH, parse_record, write_record


def main() -> None:
    try:
        runpy.run_path("/tmp/probe.py", run_name="__main__")  # noqa: S108  # Fixed private bind.
    except BaseException as exc:
        category = (
            "import_error"
            if isinstance(exc, ImportError)
            else "os_error"
            if isinstance(exc, OSError)
            else "python_error"
        )
        try:
            with open(SANDBOX_PATH, "rb") as source:
                record = parse_record(source.read(129))
            if record["category"] != "completed":
                write_record(str(record["stage_entered"]), category)
        except (OSError, ValueError):
            pass  # The host reports missing/invalid evidence; never print the exception.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
