"""
检查和下载房间的前 3 首歌曲
"""
import asyncio
import httpx


async def check_and_download_songs(room_id: str):
    """检查房间的前 3 首歌曲并触发下载"""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient() as client:
        # 1. 获取房间歌曲列表
        print(f"Checking songs for room {room_id}...")
        response = await client.get(f"{base_url}/api/rooms/{room_id}/songs/")
        print(f"Response status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"Failed to get songs: {response.status_code}")
            if response.status_code == 404:
                print("Room not found, please check room ID")
            return
        
        try:
            songs_data = response.json()
        except Exception as e:
            print(f"Failed to parse JSON: {e}")
            return
            
        print(f"Room has {len(songs_data.get('list', []))} songs")
        
        # 2. 获取前 3 首歌曲
        songs = songs_data.get("list", [])[:3]
        if not songs:
            print("No songs in room!")
            print("Please add songs to the room playlist first")
            return
        
        print(f"\nSongs to check:")
        for i, room_song in enumerate(songs, 1):
            # 从嵌套的 song 对象中获取信息
            song_obj = room_song.get("song", {})
            song_id = room_song.get("song_id")
            title = song_obj.get("title", "Unknown")
            artist = song_obj.get("artist", "Unknown")
            platform_song_id = song_obj.get("platform_song_id")
            
            print(f"  {i}. {title} - {artist} (Song ID: {song_id}, Platform ID: {platform_song_id})")
        
        # 3. 触发每首歌曲的下载
        print(f"\nStarting download tasks...")
        for i, room_song in enumerate(songs, 1):
            song_obj = room_song.get("song", {})
            song_id = room_song.get("song_id")
            title = song_obj.get("title", "Unknown")
            platform_song_id = song_obj.get("platform_song_id")
            
            if not platform_song_id:
                print(f"\n[{i}/3] Skipping {title}: no platform song ID")
                continue
            
            print(f"\n[{i}/3] Trigger download: {title}")
            response = await client.post(f"{base_url}/api/songs/cache/{song_id}")
            
            if response.status_code == 200:
                print(f"  Download task started")
            elif response.status_code == 400:
                print(f"  Song already cached: {response.json().get('detail')}")
            else:
                print(f"  Failed to trigger download: {response.status_code}")
                print(f"     {response.text}")
        
        # 4. 等待几秒后检查任务状态
        print(f"\nWaiting 5 seconds to check status...")
        await asyncio.sleep(5)
        
        # 5. 检查歌曲的缓存状态
        print(f"\nChecking cache status:")
        for i, room_song in enumerate(songs, 1):
            song_obj = room_song.get("song", {})
            song_id = room_song.get("song_id")
            title = song_obj.get("title", "Unknown")
            
            response = await client.get(f"{base_url}/api/songs/{song_id}")
            if response.status_code == 200:
                song_info = response.json()
                cached_path = song_info.get("cached_path")
                if cached_path:
                    print(f"  [CACHED] {title}: {cached_path}")
                else:
                    print(f"  [WAITING] {title}: waiting for download...")
            else:
                print(f"  [ERROR] {title}: failed to get info")
        
        print(f"\nTips:")
        print(f"  - Download tasks run in background, may take some time")
        print(f"  - Run this script again to check progress")
        print(f"  - Visit http://localhost:8000/docs for API docs")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法：python check_and_download_songs.py <room_id>")
        print("示例：python check_and_download_songs.py 2ZZKNA")
        sys.exit(1)
    
    room_id = sys.argv[1]
    print(f"Room ID: {room_id}")
    print("=" * 60)
    
    asyncio.run(check_and_download_songs(room_id))
