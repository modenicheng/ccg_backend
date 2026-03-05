from __future__ import annotations
import qqmusic_api as qapi
import asyncio
import json

cookie = '''pgv_pvid=3724488231; fqm_pvqid=08e1587c-368d-4812-a564-aabd808698f9; ts_uid=3419720830; RK=0VEgqslbSe; ptcz=f8a6b46d96067569b40987955300db61658cd1b26a5cc9bc6ed15aeadcb7ea43; yyb_muid=29F36C5010B5663D03EA7A4011B46791; pac_uid=0_xAtTFn0zRBcTG; omgid=0_xAtTFn0zRBcTG; _qimei_uuid42=19c150e3714100dfddd6428095c686fc856f7ce5f6; _qimei_fingerprint=f311837483f1b342ffd0d03050b25e16; fqm_sessionid=80ab43d5-b486-47da-9e90-193af5ea874e; pgv_info=ssid=s776653250; _qpsvr_localtk=0.7612117853608775; login_type=1; ts_refer=ADTAGh5_share_playlist; ts_last=y.qq.com/; euin=NKoqNeC5NKSA; psrf_access_token_expiresAt=1776862666; psrf_qqopenid=FE8FAC37376EFA9E825978A8089B54FD; qqmusic_key=Q_H_L_63k3NVX1rth54weOe96rURaD1FoHKwklrjef3o6YXu_IT7CLIfesxOQ-ID2JVyfLH7FOL4OKAiNNGhFOXJIvaYI3u; psrf_qqunionid=F778C6DDFFF66B42661234D4C796300B; psrf_qqaccess_token=71D3874510CD128180E4E1F6C03B4A4A; wxunionid=; tmeLoginType=2; uin=939861972; wxopenid=; qm_keyst=Q_H_L_63k3NVX1rth54weOe96rURaD1FoHKwklrjef3o6YXu_IT7CLIfesxOQ-ID2JVyfLH7FOL4OKAiNNGhFOXJIvaYI3u; psrf_musickey_createtime=1771678666; music_ignore_pskey=202306271436Hn@vBj; wxrefresh_token=; psrf_qqrefresh_token=79DCA96CDFEDF0285CF05C3A81FD53F2'''
c = {
    "pgv_pvid": "3724488231",
    "fqm_pvqid": "08e1587c-368d-4812-a564-aabd808698f9",
    "ts_uid": "3419720830",
    "RK": "0VEgqslbSe",
    "ptcz": "f8a6b46d96067569b40987955300db61658cd1b26a5cc9bc6ed15aeadcb7ea43",
    "yyb_muid": "29F36C5010B5663D03EA7A4011B46791",
    "pac_uid": "0_xAtTFn0zRBcTG",
    "omgid": "0_xAtTFn0zRBcTG",
    "_qimei_uuid42": "19c150e3714100dfddd6428095c686fc856f7ce5f6",
    "_qimei_fingerprint": "f311837483f1b342ffd0d03050b25e16",
    "fqm_sessionid": "80ab43d5-b486-47da-9e90-193af5ea874e",
    "pgv_info": "ssid=s776653250",
    "_qpsvr_localtk": "0.7612117853608775",
    "login_type": "1",
    "ts_refer": "ADTAGh5_share_playlist",
    "ts_last": "y.qq.com/",
    "euin": "NKoqNeC5NKSA",
    "psrf_access_token_expiresAt": "1776862666",
    "psrf_qqopenid": "FE8FAC37376EFA9E825978A8089B54FD",
    "qqmusic_key":
    "Q_H_L_63k3NVX1rth54weOe96rURaD1FoHKwklrjef3o6YXu_IT7CLIfesxOQ-ID2JVyfLH7FOL4OKAiNNGhFOXJIvaYI3u",
    "psrf_qqunionid": "F778C6DDFFF66B42661234D4C796300B",
    "psrf_qqaccess_token": "71D3874510CD128180E4E1F6C03B4A4A",
    "wxunionid": "",
    "tmeLoginType": "2",
    "uin": "939861972",
    "wxopenid": "",
    "qm_keyst":
    "Q_H_L_63k3NVX1rth54weOe96rURaD1FoHKwklrjef3o6YXu_IT7CLIfesxOQ-ID2JVyfLH7FOL4OKAiNNGhFOXJIvaYI3u",
    "psrf_musickey_createtime": "1771678666",
    "music_ignore_pskey": "202306271436Hn@vBj",
    "wxrefresh_token": "",
    "psrf_qqrefresh_token": "79DCA96CDFEDF0285CF05C3A81FD53F2"
}

credential = qapi.Credential.from_cookies_dict(c)

qapi.get_session().credential = credential

print(credential.refresh_key, credential.refresh_token)


async def main():
    # result = await qapi.song.get_song_urls(
    #     ["0018bm1j1Yy4Mk"],
    #     file_type=qapi.song.SongFileType.OGG_320,
    #     credential=credential)
    result = await qapi.song.get_detail(9519555384)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # print(await credential.is_expired())


async def get_song_url(mid: str):
    result = await qapi.song.get_song_urls(
        [mid], file_type=qapi.song.SongFileType.OGG_320, credential=credential)
    return result.get(mid)


# asyncio.run(main())
