import os

# Tests that don't touch the database still need a URL to build the engine.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
