"""
CRUD (Create, Read, Update, Delete) operations for the CCG backend.

This module contains database operations for managing users, rooms, songs,
songlists, and game-related data.
"""
from .audio_preload_and_token import (get_or_create_audio_token, prepare_preload_songs,
                                      validate_audio_token, update_room_song_temp_token,
                                      get_room_song_by_song_id,
                                      get_room_song_and_song_by_temp_token,
                                      update_room_current_song_index)
from .judge_related import (get_room_song_queue, get_current_song_info,
                            get_player_answers_for_judging, update_player_answer_order,
                            get_tag_group_map, save_score_record)
from .task_related import (create_task_record, get_task_record_by_task_id,
                           update_task_record)
from .room_song_related import (get_room_songs, shuffle_room_songs, add_songs_to_room,
                                remove_songs_from_room, update_room_song_order,
                                clear_room_songs, get_room_song, count_room_songs,
                                count_songlist_songs, simple_authentication,
                                fetch_room_object)
from .song_related import (create_or_update_songlist, create_or_update_song,
                           create_or_update_songs, update_song_cached_path)
from .room_state_related import (
    get_room_with_state,
    get_room_players,
    get_player_by_id,
    update_player_online_status,
    set_all_room_players_offline,
    set_room_start_position,
)
from .room_state_cache_compat import (
    load_room_state,
    save_room_state,
    get_room_players as get_room_players_cache,
    set_room_player,
    get_room_player,
    update_room_player_online_status,
    remove_room_player,
)

__all__ = [
    "get_or_create_audio_token",
    "prepare_preload_songs",
    "validate_audio_token",
    "update_room_song_temp_token",
    "get_room_song_by_song_id",
    "get_room_song_and_song_by_temp_token",
    "update_room_current_song_index",
    # Judge related
    "get_room_song_queue",
    "get_current_song_info",
    "get_player_answers_for_judging",
    "fetch_room_object",
    "update_player_answer_order",
    "get_tag_group_map",
    "save_score_record",
    # Task related
    "create_task_record",
    "get_task_record_by_task_id",
    "update_task_record",
    # RoomSong related
    "get_room_songs",
    "shuffle_room_songs",
    "add_songs_to_room",
    "remove_songs_from_room",
    "update_room_song_order",
    "clear_room_songs",
    "get_room_song",
    "count_room_songs",
    "count_songlist_songs",
    "simple_authentication",
    "fetch_room_object",
    # Song related
    "create_or_update_songlist",
    "create_or_update_song",
    "create_or_update_songs",
    "update_song_cached_path",
    # Room state related (SQL-backed)
    "get_room_with_state",
    "get_room_players",
    "get_player_by_id",
    "update_player_online_status",
    "set_all_room_players_offline",
    "set_room_start_position",
    # Room state compatibility APIs migrated from cache.room_cache
    "load_room_state",
    "save_room_state",
    "get_room_players_cache",
    "set_room_player",
    "get_room_player",
    "update_room_player_online_status",
    "remove_room_player",
]
