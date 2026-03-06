from __future__ import annotations

from typing import TypedDict

import pytest
from utils.calculate import calculate_player_scores


class PlayerAnswerData(TypedDict):
    selected_tag_ids: list[int]
    description_text: str | None


def test_calculate_player_scores_basic_tag_matching():
    """Test basic tag group matching"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': "desc1",
        },
        "1002": {
            'selected_tag_ids': [103],
            'description_text': None,
        }
    }
    tag_group_map = {
        1: [101, 102],  # Group 1 contains both correct tags
        2: [103]  # Group 2 contains wrong tag
    }
    correct_tags = [101, 102]
    correct_description_ids = []

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    assert scores["1001"] == 1  # Player 1 gets 1 point for matching group 1
    assert scores["1002"] == 0  # Player 2 gets 0 points


def test_calculate_player_scores_multiple_groups():
    """Test scoring with multiple tag groups"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 103],
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [102, 104],
            'description_text': None,
        }
    }
    tag_group_map = {
        1: [101, 102],  # Group 1: correct tags 101, 102
        2: [103, 104]  # Group 2: correct tags 103, 104
    }
    correct_tags = [101, 102, 103, 104]  # All tags correct

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 matches group 1 (tags 101 only? Actually needs both 101 and 102)
    # Player 1 has [101, 103] - group 1 requires [101, 102], not match
    # Player 2 has [102, 104] - group 1 requires [101, 102], not match
    # So no one gets points for group 1
    # Player 1 has 103 (group 2), needs [103, 104] - not match
    # Player 2 has 104 (group 2), needs [103, 104] - not match
    # So all scores should be 0
    assert scores["1001"] == 0
    assert scores["1002"] == 0


def test_calculate_player_scores_exact_group_match():
    """Test exact group matching"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [103, 104],
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102], 2: [103, 104]}
    correct_tags = [101, 102, 103, 104]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 matches group 1 exactly, gets 1 point
    # Player 2 matches group 2 exactly, gets 1 point
    assert scores["1001"] == 1
    assert scores["1002"] == 1


def test_calculate_player_scores_description_points():
    """Test description scoring"""
    answer_queue = ["1001", "1002", "1003"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [],
            'description_text': "desc1",
        },
        "1002": {
            'selected_tag_ids': [],
            'description_text': "desc2",
        },
        "1003": {
            'selected_tag_ids': [],
            'description_text': "desc3",
        }
    }
    tag_group_map = {}
    correct_tags = []
    correct_description_ids = [1002,
                               1003]  # Players 1002 and 1003 have correct descriptions

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    # Only first player in queue with correct description gets point (player 1002)
    assert scores["1001"] == 0  # Not in correct_description_ids
    assert scores["1002"] == 1  # First in queue with correct description
    assert scores[
        "1003"] == 0  # Has correct description but player 1002 got the point first


def test_calculate_player_scores_combined_tag_and_description():
    """Test combined tag and description scoring"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': "desc1",
        },
        "1002": {
            'selected_tag_ids': [103],
            'description_text': "desc2",
        }
    }
    tag_group_map = {1: [101, 102], 2: [103]}
    correct_tags = [101, 102]
    correct_description_ids = [1002]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    # Player 1 gets 1 point for tag group match
    # Player 2 gets 1 point for description (first in queue with correct description)
    assert scores["1001"] == 1
    assert scores["1002"] == 1


def test_calculate_player_scores_empty_queue():
    """Test with empty answer queue"""
    answer_queue = []
    player_answers = {}
    tag_group_map = {1: [101, 102]}
    correct_tags = [101, 102]
    correct_description_ids = [1001]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    assert scores == {}  # No players, empty dict


def test_calculate_player_scores_player_not_in_answers():
    """Test when player in queue but no answer data"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101],
            'description_text': "desc",
        }
        # Player 1002 not in player_answers
    }
    tag_group_map = {1: [101]}
    correct_tags = [101]
    correct_description_ids = []

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    assert scores["1001"] == 1  # Player 1 gets point
    assert scores["1002"] == 0  # Player 2 has no answer, score 0


def test_calculate_player_scores_multiple_correct_tags_same_group():
    """Test when multiple correct tags in same group - requires exact match"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101],  # Only one of the two correct tags
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [101, 102],  # Both correct tags
            'description_text': None,
        }
    }
    tag_group_map = {
        1: [101, 102]  # Group with two tags
    }
    correct_tags = [101, 102]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 has only [101], not exact match
    # Player 2 has [101, 102], exact match, gets 1 point
    assert scores["1001"] == 0
    assert scores["1002"] == 1


def test_calculate_player_scores_only_one_tag_group_point_per_player():
    """Test that a player can only get at most 1 point from tag groups"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102, 103, 104],  # Matches both groups
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [],
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102], 2: [103, 104]}
    correct_tags = [101, 102, 103, 104]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 should get only 1 point (for first matching group)
    # Player 2 gets 0
    assert scores["1001"] == 1
    assert scores["1002"] == 0


def test_calculate_player_scores_extra_tags_outside_group():
    """Test player with extra tags outside correct group still gets point"""
    answer_queue = ["1001"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102, 999],  # Extra tag 999 not in group
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102]}
    correct_tags = [101, 102]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player should get point for matching group (extra tags ignored)
    assert scores["1001"] == 1


def test_calculate_player_scores_tags_already_awarded():
    """Test that tags already awarded to earlier player are removed from consideration"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [101, 102],  # Same tags as player 1
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102]}
    correct_tags = [101, 102]  # Only one group worth of points

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 gets point, player 2 doesn't because tags already awarded
    assert scores["1001"] == 1
    assert scores["1002"] == 0


def test_calculate_player_scores_empty_correct_tags():
    """Test when correct_tags is empty (no tag points awarded)"""
    answer_queue = ["1001"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102]}
    correct_tags = []  # No correct tags

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # No points because no correct tags
    assert scores["1001"] == 0


def test_calculate_player_scores_empty_tag_group_map():
    """Test when tag_group_map is empty (no tag groups to match)"""
    answer_queue = ["1001"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': None,
        }
    }
    tag_group_map = {}  # No tag groups
    correct_tags = [101, 102]  # But no groups to match against

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # No points because no tag groups defined
    assert scores["1001"] == 0


def test_calculate_player_scores_player_without_selected_tags():
    """Test player with empty selected_tag_ids list"""
    answer_queue = ["1001"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [],  # No tags selected
            'description_text': None,
        }
    }
    tag_group_map = {1: [101, 102]}
    correct_tags = [101, 102]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player gets 0 points (no tags to match)
    assert scores["1001"] == 0


def test_calculate_player_scores_description_only_first_in_queue():
    """Test that only first player in queue with correct description gets point"""
    answer_queue = ["1001", "1002", "1003"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [],
            'description_text': "desc1",
        },
        "1002": {
            'selected_tag_ids': [],
            'description_text': "desc2",
        },
        "1003": {
            'selected_tag_ids': [],
            'description_text': "desc3",
        }
    }
    tag_group_map = {}
    correct_tags = []
    # All three players have correct descriptions
    correct_description_ids = [1001, 1002, 1003]

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    # Only first player (1001) gets description point
    assert scores["1001"] == 1
    assert scores["1002"] == 0
    assert scores["1003"] == 0


def test_calculate_player_scores_overlapping_tag_groups():
    """Test scoring when tags appear in multiple groups (overlapping)"""
    answer_queue = ["1001", "1002"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],  # Matches group 1
            'description_text': None,
        },
        "1002": {
            'selected_tag_ids': [101,
                                 103],  # Could match group 2 but tag 101 already used
            'description_text': None,
        }
    }
    tag_group_map = {
        1: [101, 102],  # Group 1: tags 101, 102
        2: [101, 103]  # Group 2: overlapping tag 101
    }
    correct_tags = [101, 102, 103]  # Three correct tags

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=[],
    )

    # Player 1 matches group 1 exactly (tags 101, 102) - gets 1 point
    # Tags 101 and 102 are removed from remaining_correct_tags
    # Player 2 has [101, 103] but tag 101 is already used, remaining_correct_tags = [103]
    # Group 2 requires [101, 103] - not match
    # So player 2 gets 0 points
    assert scores["1001"] == 1
    assert scores["1002"] == 0


def test_calculate_player_scores_player_with_description_and_tags():
    """Test player can get both tag point and description point"""
    answer_queue = ["1001"]
    player_answers: dict[str, PlayerAnswerData] = {
        "1001": {
            'selected_tag_ids': [101, 102],
            'description_text': "desc1",
        }
    }
    tag_group_map = {1: [101, 102]}
    correct_tags = [101, 102]
    correct_description_ids = [1001]  # Same player has correct description

    scores = calculate_player_scores(
        answer_queue=answer_queue,
        player_answers=player_answers,
        tag_group_map=tag_group_map,
        correct_tags=correct_tags,
        correct_description_ids=correct_description_ids,
    )

    # Player should get 1 point for tag group and 1 point for description
    assert scores["1001"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
