
def calculate_player_scores(
    answer_queue: list[str],
    player_answers: dict[str, dict[str, list]],
    tag_group_map: dict[int, list[int]],
    correct_tags: list[int],
    correct_description_ids: list[int],
) -> dict[str, int]:
    """Calculate player scores based on answer queue and correct answers.

    Args:
        answer_queue: List of player IDs in answer order (strings)
        player_answers: Dict mapping player_id to answer data
        tag_group_map: Dict mapping tag_group_id to list of tag IDs
        correct_tags: List of correct tag IDs
        correct_description_ids: List of player IDs with correct descriptions

    Returns:
        Dict mapping player_id to score delta for this round
    """
    player_scores = {}
    # Initialize scores for all players in answer queue
    for player_id in answer_queue:
        player_scores[player_id] = 0

    # Create a copy of correct_tags to modify as we award points
    remaining_correct_tags = correct_tags.copy()

    # Tag group scoring: for each player in answer order
    for player_id in answer_queue:
        if player_id not in player_answers:
            continue

        player_answer = player_answers[player_id]
        selected_tags = player_answer.get('selected_tag_ids', [])

        # Check each tag group
        for tag_group_id, group_tags in tag_group_map.items():
            # Find correct tags in this group
            group_correct_tags = [
                tag for tag in remaining_correct_tags if tag in group_tags
            ]
            if not group_correct_tags:
                continue

            # Check if player selected exactly the correct tags for this group
            player_group_tags = [
                tag for tag in selected_tags if tag in group_tags
            ]
            if player_group_tags == group_correct_tags:
                # Award 1 point
                player_scores[player_id] += 1
                # Remove these tags from consideration
                for tag in group_correct_tags:
                    if tag in remaining_correct_tags:
                        remaining_correct_tags.remove(tag)
                # Move to next player (only one point per player from tag groups)
                break

    # Description scoring: for each player in answer order
    if correct_description_ids:
        for player_id in answer_queue:
            if player_id not in player_answers:
                continue

            # Check if player is in correct_description_ids
            if int(player_id) in correct_description_ids:
                player_scores[player_id] += 1
                # Only first matching player gets description point
                break

    return player_scores

