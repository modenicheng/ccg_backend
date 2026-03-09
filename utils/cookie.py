from __future__ import annotations

"""
Cookie解析工具
提供HTTP Cookie字符串解析功能
"""


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    """
    解析Cookie字符串为字典

    Args:
        cookie_str: Cookie字符串，如 "key1=value1; key2=value2"

    Returns:
        Cookie键值对字典
    """
    cookies: dict[str, str] = {}
    for item in cookie_str.split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cookies[key] = value.strip()
    return cookies
