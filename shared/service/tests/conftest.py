"""`shared.service.engine` pulls in `app.core.config.settings` (for
`DB_PATH`), which requires `API_KEY`/`DB_PATH` env vars with no defaults —
set here, at conftest module scope, before pytest imports any test module
that imports a service."""

import os

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("DB_PATH", "/tmp/xffl-service-tests.db")
