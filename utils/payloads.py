from schemas.room import RoomStateInitMessage, RoomStateInitData, RoomStatePlayerItem, RoomStateTagGroupItem, RoomStateTagItem
from db.models import Room

def build_roomstate_init_payload(room: Room) -> RoomStateInitMessage:
    host_user = next((u for u in room.users if u.is_owner), None)

    tag_groups: list[RoomStateTagGroupItem] = []
    unique_tags: dict[int, RoomStateTagItem] = {}
    for group in room.tag_groups:
        group_tags: list[RoomStateTagItem] = []
        for tag in group.tags:
            tag_item = RoomStateTagItem(
                id=tag.id,
                name=tag.name,
            )
            group_tags.append(tag_item)
            unique_tags[tag.id] = tag_item

        tag_groups.append(
            RoomStateTagGroupItem(
                id=group.id,
                name=group.name,
                description=group.description,
                tags=group_tags,
            ))

    message = RoomStateInitMessage(data=RoomStateInitData(
        room_id=room.id,
        title=room.title,
        status=int(room.status),
        host=host_user.username if host_user else None,
        owner=host_user.username if host_user else None,
        host_player_id=str(host_user.id) if host_user else "",
        players=[
            RoomStatePlayerItem(
                id=u.id,
                username=u.username,
                is_owner=u.is_owner,
            ) for u in room.users
        ],
        tag_groups=tag_groups,
        tags=list(unique_tags.values()),
    ))

    return message
