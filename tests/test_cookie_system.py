#!/usr/bin/env python
"""Test script for Cookie rotation system."""

from __future__ import annotations

import asyncio
import logging

from mq.tasks import (
    COOKIE_POOL_MANAGER,
    COOKIE_ROTATION_MANAGER,
    COOKIE_REFRESH_SERVICE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_cookie_system():
    """Test cookie pool and rotation system."""
    logger.info("=" * 60)
    logger.info("Testing Cookie Rotation System")
    logger.info("=" * 60)

    # Test 1: Check pool
    logger.info("\n[Test 1] Cookie Pool Status")
    logger.info(f"  - Pool initialized: {COOKIE_POOL_MANAGER is not None}")
    logger.info(f"  - Cookies in pool: {len(COOKIE_POOL_MANAGER.pool)}")
    logger.info(f"  - Pool details: {COOKIE_POOL_MANAGER.to_dict()}")

    # Test 2: Check rotation manager
    logger.info("\n[Test 2] Cookie Rotation Manager")
    if COOKIE_ROTATION_MANAGER:
        logger.info("  - Manager initialized: True")
        current_cookie = COOKIE_ROTATION_MANAGER.get_current()
        logger.info(
            f"  - Current cookie: {current_cookie.cookie_id if current_cookie else None}"
        )
        logger.info(f"  - Rotation state: {COOKIE_ROTATION_MANAGER.get_state_dict()}")
    else:
        logger.info("  - Manager not initialized (no cookies configured)")

    # Test 3: Check refresh service
    logger.info("\n[Test 3] Cookie Refresh Service")
    if COOKIE_REFRESH_SERVICE:
        logger.info("  - Refresh service initialized: True")
        refresh_result = await COOKIE_REFRESH_SERVICE.check_and_refresh_expired()
        logger.info(f"  - Refresh check result: {refresh_result}")
    else:
        logger.info("  - Refresh service not initialized")

    # Test 4: Simulate rotation
    if COOKIE_ROTATION_MANAGER and len(COOKIE_POOL_MANAGER.pool) > 1:
        logger.info("\n[Test 4] Cookie Rotation Test")
        current = COOKIE_ROTATION_MANAGER.get_current()
        logger.info(
            f"  - Current before rotation: {current.cookie_id if current else None}")
        COOKIE_ROTATION_MANAGER.rotate(reason="test")
        current_after = COOKIE_ROTATION_MANAGER.get_current()
        logger.info(
            f"  - Current after rotation: {current_after.cookie_id if current_after else None}"
        )
        logger.info(f"  - State: {COOKIE_ROTATION_MANAGER.get_state_dict()}")
    else:
        logger.info("\n[Test 4] Cookie Rotation Test - Skipped (need 2+ cookies)")

    logger.info("\n" + "=" * 60)
    logger.info("Test Complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_cookie_system())
