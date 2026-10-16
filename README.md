# qbittorrent-nox-easywebapp

A small LAN web page to search torrents through qBittorrent, plus a qBittorrent-nox
container that automatically sorts finished downloads into `Films`, `Musics` and `SerieTv`.

- **torrent-web**: paste one or more searches (one per line), filter the results
  (type, seeders, size, text) and add them to qBittorrent with one click.
  Python standard library only, protected by an optional password.
- **qbittorrent-nox**: official `qbittorrentofficial/qbittorrent-nox` image.
  When a torrent finishes, `scripts/qbt-sort.py` moves it (via the Web API, so seeding
  continues) to:
  - `/Musics` if it is mostly audio
  - `/SerieTv` if the names look like episodes (`S01E02`, `1x02`, `Season 1`, …)
  - `/Films/Title (Year)/` otherwise

Based on the official qBittorrent-nox Docker image:
[qbittorrent/docker-qbittorrent-nox (main)](https://github.com/qbittorrent/docker-qbittorrent-nox/tree/main)
· qBittorrent itself: [qbittorrent/qBittorrent](https://github.com/qbittorrent/qBittorrent).

## Setup

```sh
cp .env.example .env        # then change passwords and ports
mkdir -p qbittorrent/config downloads media/Films media/Musics media/SerieTv
docker compose up -d --build
```

On the first start qBittorrent prints a temporary Web UI password:

```sh
docker logs qbittorrent-nox
```

Open `http://SERVER-IP:<QBT_WEBUI_PORT>`, log in with `admin` and that password, then in
**Tools → Options**:

1. **Web UI → Authentication**: set the user and password you put in `.env`
   (`QBIT_USER` / `QBIT_PASS`). Tick *Bypass authentication for clients on localhost*
   (the sort script uses it).
2. **Downloads → Run external program → Run on torrent finished**:
   ```
   python3 /scripts/qbt-sort.py "%K"
   ```
3. **Search** tab: install the search plugins you want (the web page uses the enabled ones).

Then open the page at `http://SERVER-IP:<WEB_PORT>`.

## Notes

- The sort log is `qbittorrent/config/qbt-sort.log`. Test without moving anything:
  `docker exec qbittorrent-nox python3 /scripts/qbt-sort.py <hash> --dry-run`
- `PUID`/`PGID` (default 1000) must be able to write the `media/` folders.
- To use a qBittorrent already installed on the host, remove the `qbittorrent-nox`
  service and set `QBIT_URL=http://host.docker.internal:<port>` in `.env`.
