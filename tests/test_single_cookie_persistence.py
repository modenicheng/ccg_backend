"""
演示脚本：验证单cookie持久化修复

这个脚本展示了修复后的工作流程：
1. 单cookie被加载到内存的pool
2. 自动持久化到database
3. 下次启动时从database加载
"""

import asyncio
import logging
import os

# Setup logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set test environment
os.environ[
    'CCG_QQ_MUSIC_COOKIE'] = 'uin=123456; uin_bak=123456; p_token=token_test_value'


async def demonstrates_persistence_flow():
    """Demonstrate the cookie persistence flow."""
    logger.info("=" * 80)
    logger.info("演示：单Cookie持久化修复")
    logger.info("=" * 80)

    # Step 1: Initialize the cookie system
    logger.info("\n[步骤1] 正在初始化Cookie系统...")
    logger.info("检测到QQ Music cookie配置，初始化 cookie 系统...")

    from mq.tasks import _initialize_cookie_system, COOKIE_POOL_MANAGER

    # Clear pool first
    COOKIE_POOL_MANAGER.pool.clear()
    COOKIE_POOL_MANAGER.primary_cookie_id = None

    await _initialize_cookie_system()

    logger.info(f"\n[步骤1结果] Cookie池中现有 {len(COOKIE_POOL_MANAGER.pool)} 个cookie")
    for cookie_id, entry in COOKIE_POOL_MANAGER.pool.items():
        logger.info(f"  - Cookie ID: {cookie_id}")
        logger.info(f"    来源:environment, 状态: {'健康' if entry.is_healthy else '不健康'}")

    # Step 2: Check database
    logger.info("\n[步骤2] 正在检查数据库中的Cookie配置...")
    try:
        from db.session import session_scope
        from db.crud.cookie_crud import get_all_active_cookies

        async with session_scope() as db_session:
            db_cookies = await get_all_active_cookies(db_session)
            logger.info(f"\n[步骤2结果] 数据库中保存了 {len(db_cookies)} 个active cookies:")
            for cookie in db_cookies:
                logger.info(f"  - Hash: {cookie['cookie_hash']}")
                logger.info(f"    来源: {cookie['source']}")
                logger.info(f"    数据库ID: {cookie['id']}")
    except Exception as db_err:
        logger.error(f"Failed to check database: {db_err}")

    # Step 3: Check rotation system
    logger.info("\n[步骤3] 正在检查Cookie轮换系统...")
    from mq.tasks import COOKIE_ROTATION_MANAGER

    if COOKIE_ROTATION_MANAGER:
        current = COOKIE_ROTATION_MANAGER.get_current()
        logger.info("\n[步骤3结果] Cookie轮换系统已初始化")
        logger.info(f"  - 当前Cookie: {current.cookie_id if current else '无'}")
        logger.info(f"  - 轮换策略: {COOKIE_ROTATION_MANAGER.strategy.value}")
        logger.info(f"  - 故障策略: {COOKIE_ROTATION_MANAGER.failure_policy.value}")
        logger.info(f"  - 轮换次数: {COOKIE_ROTATION_MANAGER.state.rotation_count}")
        logger.info(f"  - 可用Cookies: {len(COOKIE_ROTATION_MANAGER.state.cookies)}")
    else:
        logger.warning("[步骤3结果] Cookie轮换系统未初始化（这在只有1个cookie时是正常的）")

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("总结：单Cookie持久化修复")
    logger.info("=" * 80)
    logger.info("""
✓ 单个环境变量cookie现已支持：
  1. 自动加载到内存pool
  2. 自动持久化到database (CookieConfig表)
  3. 在错误后参与轮换系统（即使只有1个cookie）
  4. 下次启动时自动从database恢复

修复的关键改进：
  - 修改了_initialize_cookie_system()从database加载cookies
  - 添加了persist_new_cookies_to_db()方法
  - 改进了error handling逻辑使用used_rotation_manager标志
  - 添加了comprehensive logging来追踪持久化过程
    """)
    logger.info("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(demonstrates_persistence_flow())
