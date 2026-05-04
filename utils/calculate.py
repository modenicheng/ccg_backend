"""
分数计算工具
提供玩家分数计算相关功能
"""
from __future__ import annotations


def calculate_player_scores(
    answer_queue: list[int],
    player_answers: dict[int, dict[str, list]],
    tag_group_map: dict[int, list[int]],
    correct_tags: list[int],
    correct_description_ids: list[int],
) -> dict[int, int]:
    """Calculate player scores based on answer queue and correct answers.

    计分规则：
    - 每个正确答案标签独立计分：按抢答顺序遍历玩家，第一个包含该标签的玩家得 1 分
    - 精准描述：按抢答顺序遍历玩家，第一个描述 ID 匹配正确答案的玩家得 2 分（即使后面还有其他玩家也答对）

    Args:
        answer_queue: List of player IDs in answer order (integers)
        player_answers: Dict mapping player_id to answer data
        tag_group_map: Dict mapping tag_group_id to list of tag IDs
        correct_tags: List of correct tag IDs
        correct_description_ids: List of correct description IDs (from player answer IDs)

    Returns:
        Dict mapping player_id to score delta for this round
    """
    player_scores: dict[int, int] = {}
    for player_id in answer_queue:
        player_scores[player_id] = 0

    # 标签计分：对每个正确答案标签，按抢答顺序遍历玩家
    for correct_tag_id in correct_tags:
        for player_id in answer_queue:
            if player_id not in player_answers:
                continue

            player_answer = player_answers[player_id]
            selected_tags = player_answer.get("selected_tag_ids", [])

            if correct_tag_id in selected_tags:
                player_scores[player_id] += 1
                break  # 该标签已计分，继续处理下一个正确答案标签

    # 描述计分：按抢答顺序遍历玩家，第一个匹配的玩家得分
    if correct_description_ids:
        correct_desc_set = set(correct_description_ids)

        for player_id in answer_queue:
            if player_id not in player_answers:
                continue

            if player_id in correct_desc_set:
                player_scores[player_id] += 2
                break  # 只给第一个匹配的玩家加分，得 2 分

    return player_scores
