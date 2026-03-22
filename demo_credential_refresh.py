"""
演示脚本：Credential刷新功能

展示如何使用credential.refresh()方法来自动刷新过期的cookies
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


async def demonstrate_credential_refresh():
    """演示credential刷新的工作流程"""
    logger.info("=" * 80)
    logger.info("演示：Credential刷新功能")
    logger.info("=" * 80)

    from utils.cookie_pool import CookiePoolManager

    # Step 1：初始化cookie pool
    logger.info("\n[步骤1] 初始化Cookie池...")
    cookie_pool = CookiePoolManager()

    test_cookie = 'uin=test123; p_token=test_token; skey=@test; qm_keyst=test'
    await cookie_pool.load_from_sources(single_cookie=test_cookie)

    if cookie_pool.pool:
        for cookie_id, entry in cookie_pool.pool.items():
            logger.info(f"  ✓ Loaded cookie: {cookie_id}")
            logger.info("    Credential fields:")
            logger.info(f"      - openid: {entry.credential.openid}")
            logger.info(f"      - musicid: {entry.credential.musicid}")
            logger.info(f"      - musickey: {entry.credential.musickey}")
            logger.info(f"      - expired_at: {entry.credential.expired_at}")

    # Step 2：检查credential过期状态
    logger.info("\n[步骤2] 检查Credential过期状态...")
    for cookie_id, entry in cookie_pool.pool.items():
        try:
            is_expired = await entry.credential.is_expired()
            logger.info(f"  Cookie {cookie_id}: expired={is_expired}")
        except Exception as e:
            logger.warning(f"  无法检查过期状态: {e}")

    # Step 3：检查是否可以刷新
    logger.info("\n[步骤3] 检查是否可以刷新Credential...")
    for cookie_id, entry in cookie_pool.pool.items():
        try:
            can_refresh = await entry.credential.can_refresh()
            logger.info(f"  Cookie {cookie_id}: can_refresh={can_refresh}")

            if can_refresh:
                logger.info("    使用credential.refresh()方法尝试刷新...")
                old_expiry = entry.credential.expired_at
                success = await entry.credential.refresh()
                logger.info(f"    刷新结果: {'✓ 成功' if success else '✗ 失败'}")
                if success:
                    new_expiry = entry.credential.expired_at
                    logger.info(f"    过期时间变化: {old_expiry} -> {new_expiry}")
            else:
                logger.info("    Credential暂时无法刷新（可能是refresh_token不可用）")
        except Exception as e:
            logger.warning(f"  检查/刷新出错: {e}")

    # Step 4：展示定期任务
    logger.info("\n[步骤4] 展示定期刷新任务...")
    logger.info("  设置的定期任务信息：")
    logger.info("    ✓ 任务名称: refresh_credentials_periodic")
    logger.info("    ✓ 触发周期: 每30分钟执行一次")
    logger.info("    ✓ 方式: crontab(minute='*/30')")
    logger.info("    ✓ 调用方法: await credential.refresh()")
    logger.info("    ✓ 日志记录: 记录所有刷新事件到CookieRefreshLog表")

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("总结：Credential刷新功能")
    logger.info("=" * 80)
    logger.info("""
✓ Credential刷新流程：
  1. 定期任务每30分钟检查一次pool中的所有credentials
  2. 对于过期的credential调用 credential.is_expired()
  3. 检查该credential是否可以刷新 credential.can_refresh()
  4. 如果可以刷新，调用 credential.refresh() 刷新
  5. 刷新成功后：
     - 更新credential的access_token和过期时间
     - 标记CookieEntry为健康状态
     - 记录到数据库CookieRefreshLog表

✓ 关键方法（来自qqmusic_api库）：
  - credential.is_expired() -> bool：检查是否过期
  - credential.can_refresh() -> bool：检查是否可以刷新
  - credential.refresh() -> bool：执行刷新（返回成功/失败）

✓ 定期任务：
  - @huey.periodic_task(crontab(minute='*/30'))
  - 状态日志：INFO/DEBUG级别追踪所有状态变化
  - 数据库记录：log_refresh_event()
    """)
    logger.info("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(demonstrate_credential_refresh())
