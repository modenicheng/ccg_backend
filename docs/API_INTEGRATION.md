# QQ 音乐 API 集成文档

**此文档适用于官方 API 它需要 tmd 审核**，所以我们可能需要转向三方接口。

本文档整理了猜猜歌系统中需要用到的 QQ 音乐 API 调用方法，包括歌单信息获取和音频播放链接获取等核心功能。

> 官方文档参考：[QQ音乐开发者平台](https://developer.y.qq.com/docs/openapi#/)

## 1. 获取歌单中歌曲列表

### 功能说明

获取歌单中歌曲列表，接口不需要登录。

注意：根据接口返回的 `song_type` 标识歌曲类型，非库内歌曲合作方直接置灰，不用请求歌曲详情。

### 命令字

`fcg_music_custom_get_songlist_detail.fcg`

### 输入参数

| 输入参数 | 含义 | 参数是否必传 |
|---------|------|-------------|
| dissid  | 操作的歌单id | 是 |
| page    | 页码，取值从0开始 | 否 |
| page_size | 每页的数量，最大30 | 否 |

### 返回数据

| 参数名称 | 类型 | 描述 |
|---------|------|------|
| ret | int | 返回码 |
| msg | string | 如果错误，返回错误信息 |
| diss_id | uint64 | 歌单id |
| diss_title | string | 歌单title |
| pic_url | string | 歌单图片 |
| total_num | int | 歌单歌曲数量 |
| song_list | 数组 | 歌曲集合 |
| song_id | uint64 | 歌曲id |
| song_name | string | 歌曲名 |
| song_title | string | 歌曲标题 |
| song_mid | string | 歌曲mid |
| album_id | uint64 | 专辑id |
| album_name | string | 专辑名字 |
| song_play_time | uint32 | 歌曲播放时长 |
| singer_id | uint32 | 歌手id |
| singer_name | string | 歌手名字 |
| qqmusic_flag | int | 是否能够播放歌曲，0：不能播放（直接置灰不用请求歌曲详情），1：可以播放 |
| song_type | int | 标识歌曲类型 3/13-用户可播放歌曲 其他类型-不能播放（直接置灰，不用请求歌曲详情） |
| has_more | int | 是否有下一页 |

### 返回码说明

| 返回码 | 说明 |
|-------|------|
| 0 | 成功 |
| 100426 | 非法歌单ID参数 |
| 100431 | 获取歌曲列表失败（歌单不存在） |

### 请求示例

```
https://openrpc.music.qq.com/rpc_proxy/fcgi-bin/music_open_api.fcg?opi_cmd=fcg_music_custom_get_songlist_detail.fcg&app_id=xxxxxxxxxxx&timestamp=1555067641&sign=1ebf9d123a74446bb77a2ec2e9533c5f&dissid=xxx&page=1&page_size=10
```

## 2. 批量获取歌曲信息

### 功能说明

获取歌曲详情信息,包含歌曲的基本信息、播放地址等。

### 命令字

`fcg_music_custom_get_song_info_batch.fcg`

### 输入参数

| 输入参数 | 参数是否必传 | 含义 |
|---------|-------------|------|
| song_mid | 否 | 表示歌曲mid，多个mid用逗号分割（优先判断）上限50 |
| song_id | 否 | 表示歌曲id，多个mid用逗号分割, 上限50 |

> 注：song_id 与 song_mid 必传其一。

### 返回数据

| 参数名称 | 类型 | 描述 |
|---------|------|------|
| ret | int | 返回码 |
| msg | string | 如果错误，返回错误信息 |
| songlist | 数组 | 歌曲集合 |
| album_id | uint64 | 专辑id |
| album_name | string | 专辑名 |
| album_pic | string | 专辑封面 |
| playable | int | 1：表示能播放，0：表示不能播放 |
| singer_id | string | 歌手id |
| singer_name | string | 歌手名 |
| song_play_time | int | 播放总时间 |
| song_id | int | 歌曲id |
| song_mid | string | 歌曲mid |
| song_name | string | 歌曲名 |
| song_title | string | 歌曲标题 |
| song_play_url | string | 流畅品质流媒体url |
| song_play_url_standard | string | 标准品质流媒体url |
| song_play_url_hq | string | 高品质流媒体url |
| unplayable_code | int | 该字段标识不能播放的原因代码 |
| unplayable_msg | string | 不能播放原因描述语 |
| try_playable | int | 该字段标识是否有试听权限：0：没有试听权限；1：有试听权限 |
| try_30s_url | string | 30秒试听播放链接。当try_playable为1时，则该播放链接有效 |
| vkeyLeftSec | int | 播放链接剩余时长，单位秒 |

### 返回码说明

| 返回码 | 说明 |
|-------|------|
| 0 | 成功 |
| 100001 | 获取歌曲信息失败 |

### 请求示例

```
https://openrpc.music.qq.com/rpc_proxy/fcgi-bin/music_open_api.fcg?opi_cmd=fcg_music_custom_get_song_info_batch.fcg&app_id=***&app_key=xxxxxxxxxxx&timestamp=1532921851&sign=xxxxxxxxxxx&song_mid=xxxx,xxxx
```

## 3. 集成注意事项

1. **错误处理**：需要处理各种返回码和错误信息，确保系统稳定运行。

2. **缓存策略**：为了提高性能和减少 API 调用次数，建议对获取的歌曲信息和播放链接进行缓存。

3. **播放链接时效性**：注意播放链接的时效性（vkeyLeftSec 字段），过期后需要重新获取。

4. **歌曲可用性**：根据返回的 `playable`、`song_type` 等字段判断歌曲是否可用，不可用的歌曲需要做相应处理。

5. **分页处理**：获取歌单列表时需要正确处理分页逻辑，确保能获取完整的歌曲列表。

## 4. 代码实现建议

1. **API 客户端**：创建专门的 QQ 音乐 API 客户端类，封装所有 API 调用方法。

2. **异步请求**：使用异步请求库（如 aiohttp）进行 API 调用，提高性能。

3. **重试机制**：实现请求重试机制，处理网络波动等临时错误。

4. **参数验证**：对输入参数进行验证，确保 API 调用的正确性。

5. **日志记录**：记录 API 调用日志，便于调试和问题定位。

6. **数据模型**：创建对应的数据模型，方便数据的处理和使用。

通过以上 API 集成，可以实现歌单导入、歌曲信息获取和音频播放等核心功能，为猜猜歌系统提供良好的用户体验。
