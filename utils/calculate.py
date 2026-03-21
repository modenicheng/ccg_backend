"""
分数计算工具
提供玩家分数计算相关功能
"""
from __future__ import annotations


def calculate_player_scores(
    answer_queue: list[str],
    player_answers: dict[str, dict[str, list]],
    tag_group_map: dict[int, list[int]],
    correct_tags: list[int],
    correct_description_ids: list[int],
) -> dict[str, int]:
    """Calculate player scores based on answer queue and correct answers.

    计分规则：
    - 每个命中标签组计1分（需精确匹配该组所有标签）
    - 同一标签组仅最先抢答者得分
    - 同一玩家在标签组上最多得1分（即使匹配多个标签组）
    - 精准描述命中计1分

    Args:
        answer_queue: List of player IDs in answer order (strings)
        player_answers: Dict mapping player_id to answer data
        tag_group_map: Dict mapping tag_group_id to list of tag IDs
        correct_tags: List of correct tag IDs
        correct_description_ids: List of player IDs with correct descriptions

    Returns:
        Dict mapping player_id to score delta for this round
    """
    player_scores: dict[str, int] = {}
    for player_id in answer_queue:
        player_scores[player_id] = 0

    awarded_tags: set[int] = set()
    player_awarded_tag: set[str] = set()

    for player_id in answer_queue:
        if player_id in player_awarded_tag:
            continue

        if player_id not in player_answers:
            continue

        player_answer = player_answers[player_id]
        selected_tags = set(player_answer.get("selected_tag_ids", []))

        for group_tags in tag_group_map.values():
            group_tag_set = set(group_tags)
            required_tags = set(correct_tags) & group_tag_set

            if not required_tags:
                continue

            if selected_tags >= required_tags:
                can_score = True
                for tag in required_tags:
                    if tag in awarded_tags:
                        can_score = False
                        break

                if can_score:
                    player_scores[player_id] += 1
                    awarded_tags.update(required_tags)
                    player_awarded_tag.add(player_id)
                    break

    if correct_description_ids:
        correct_desc_set = set(correct_description_ids)

        for player_id in answer_queue:
            if player_id not in player_answers:
                continue

            if int(player_id) in correct_desc_set:
                player_scores[player_id] += 1
                break

    return player_scores
