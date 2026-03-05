from __future__ import annotations
from db.session import init_db
import asyncio
from utils import get_logger

logger = get_logger(__name__)


async def main():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully!")


if __name__ == "__main__":
    asyncio.run(main())
