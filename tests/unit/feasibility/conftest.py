"""Make the standalone spike scripts importable under the normal pytest entry point."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
