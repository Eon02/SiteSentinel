import os

# Tests must never pick up a developer's backend/.env or shell settings: that file holds real
# SMTP credentials and webhook URLs, and tests would send real email through them.
for key in list(os.environ):
    if key.startswith(("SMTP_", "BRIEFING", "FLOOD_CHECK")):
        del os.environ[key]

# Tests that don't touch the database still need a URL to build the engine.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from app.config import Settings, get_settings  # noqa: E402

Settings.model_config["env_file"] = None
get_settings.cache_clear()
