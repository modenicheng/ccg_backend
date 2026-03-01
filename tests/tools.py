import random


def random_string(length: int = 8, perfix: str = "") -> str:
    """生成随机字符串"""
    letters = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return perfix + ''.join(random.choice(letters) for _ in range(length))
