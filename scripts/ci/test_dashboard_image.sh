#!/bin/sh
# Inspect the packaged runtime without credentials, network access or a writable root.
set -eu
image=${1:?dashboard image required}
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 128m \
  --entrypoint python "$image" -c '
import importlib.util
import os
from pathlib import Path
from knowledge_vault_dashboard.app import create_app
from knowledge_vault_dashboard.assets import Assets
from knowledge_vault_dashboard.config import Settings

assert os.getuid() == 10001
for module in ("knowledge_vault", "sqlalchemy", "psycopg", "torch", "sentence_transformers"):
    assert importlib.util.find_spec(module) is None, module
assert not Path("/usr/local/bin/node").exists()
settings = Settings()
assert not settings.enabled
assets = Assets()
assert assets.is_page("/dashboard/") and assets.files
assert "__KV_AUTH_MODE__" in assets.index
assert Path("/usr/share/knowledge-vault-dashboard/frontend.sbom.json").is_file()
app = create_app(settings)
print("Dashboard runtime: non-root, packaged assets, disabled, no core/database/model runtime")
'
