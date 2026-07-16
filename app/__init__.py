"""JobRadar app package.

Load .env as early as possible — before any submodule (notably app.db) reads
environment variables to build the database engine. This makes DB and API config
in .env available regardless of import order.
"""
from dotenv import load_dotenv

load_dotenv()
