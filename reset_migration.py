#!/usr/bin/env python3
from __future__ import annotations
"""Reset migration version in database."""

import sqlite3
from utils import get_logger

logger = get_logger(__name__)

conn = sqlite3.connect("data/game.db")
cursor = conn.cursor()

# Check current version
cursor.execute("SELECT version_num FROM alembic_version")
result = cursor.fetchone()
logger.info(f"Current version: {result}")

# Update to 55dce11b4da2
cursor.execute("DELETE FROM alembic_version")
cursor.execute(
    "INSERT INTO alembic_version (version_num) VALUES ('55dce11b4da2')")
conn.commit()

logger.info("Migration version reset to 55dce11b4da2")

conn.close()
