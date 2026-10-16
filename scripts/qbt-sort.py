#!/usr/bin/env python3
"""
qbt-sort.py - sorts completed torrents into /Films, /Musics, /SerieTv.

Run by qBittorrent ("Run external program on torrent finished"):
    python3 /scripts/qbt-sort.py "%K"
(%K = torrent ID, also valid for v2 torrents; %I is "-" for v2 torrents)

Uses the Web API (localhost, no login: "bypass_local_auth"; port from QBT_WEBUI_PORT
or from qBittorrent.conf) and torrents/setLocation,
so qBittorrent moves the files itself and keeps seeding from the new location.

Test without moving anything:  python3 qbt-sort.py <hash> --dry-run
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

LOG = "/config/qbt-sort.log"
CONF = "/config/qBittorrent/config/qBittorrent.conf"


def webui_port():
    """QBT_WEBUI_PORT does not reach the script (the image's doas clears the environment):
    if missing, read it from qBittorrent.conf."""
    if os.getenv("QBT_WEBUI_PORT"):
        return os.environ["QBT_WEBUI_PORT"]
    try:
        with open(CONF) as f:
            for line in f:
                if line.startswith("WebUI\\Port="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "8082"


API = "http://127.0.0.1:%s/api/v2/" % webui_port()

DEST = {"video_movie": "/Films", "video_series": "/SerieTv", "audio": "/Musics"}

EXT_VIDEO = {"mkv", "mp4", "avi", "m4v", "mov", "wmv", "mpg", "mpeg", "ts", "m2ts", "webm", "flv", "vob", "divx"}
EXT_AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "wma", "alac", "ape", "aiff", "dsf"}

# S01E02, S01, 1x02, Season 1/Stagione 2, "Complete Series", Ep 01
# (season/stagione only when followed by a number: "The Four Seasons" is a movie)
RE_SERIES = re.compile(
    r"(\bS\d{1,2}\s?E\d{1,3}\b|\bS\d{1,2}\b|\b\d{1,2}x\d{2,3}\b|"
    r"\bseasons?[ ._\-]?\d{1,2}\b|\bstagion[ei][ ._\-]?\d{1,2}\b|"
    r"complete.?series|serie\s?(tv|completa)|\bep(isode)?\s?\d{1,3}\b)",
    re.IGNORECASE,
)

# "Title.2018.1080p..." -> title + year (takes the last year: "Blade.Runner.2049.2017")
RE_YEAR = re.compile(r"^(.+)[ ._\-\(\[]((?:19|20)\d{2})(?=$|[ ._\-\)\]])")
# no year: cut at the first quality/rip tag
RE_TAG = re.compile(
    r"[ ._\-\(\[](2160p|1080p|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|"
    r"hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|ita|eng|multi)\b",
    re.IGNORECASE,
)


def movie_folder(name):
    """'BlacKkKlansman.2018.1080p.BluRay' -> 'BlacKkKlansman (2018)'"""
    if "." in name and name.rsplit(".", 1)[1].lower() in EXT_VIDEO:
        name = name.rsplit(".", 1)[0]
    m = RE_YEAR.match(name)
    if m:
        title, year = m.group(1), m.group(2)
    else:
        title, year = RE_TAG.split(name, 1)[0], None
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r'[\\/:*?"<>|\[\]\(\)]', "", title).strip(" -")
    if not title:
        title = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    return "%s (%s)" % (title, year) if year else title


def log(msg):
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def api(path, data=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    with urllib.request.urlopen(API + path, body, timeout=30) as r:
        return r.read().decode()


def torrent_info(hash_):
    info = json.loads(api("torrents/info?" + urllib.parse.urlencode({"hashes": hash_})))
    return info[0] if info else None


def wait_for_move(hash_, max_seconds=3600):
    """Wait until qBittorrent has finished moving the files (e.g. from /downloads/temp)."""
    t = torrent_info(hash_)
    for _ in range(max_seconds // 5):
        if t is None or t["state"] != "moving":
            break
        time.sleep(5)
        t = torrent_info(hash_)
    return t


def root_folder(files):
    """Name of the torrent's top folder, if all files are inside a single one."""
    roots = {f["name"].split("/", 1)[0] for f in files}
    if len(roots) == 1 and all("/" in f["name"] for f in files):
        return roots.pop()
    return None


def classify(name, files):
    size = {"video": 0, "audio": 0}
    series_videos = 0
    for f in files:
        ext = f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else ""
        if ext in EXT_VIDEO:
            size["video"] += f["size"]
            if RE_SERIES.search(os.path.basename(f["name"])):
                series_videos += 1
        elif ext in EXT_AUDIO:
            size["audio"] += f["size"]

    if size["video"] == 0 and size["audio"] == 0:
        return None
    if size["audio"] > size["video"]:
        return "audio"
    if RE_SERIES.search(name) or series_videos >= 2:
        return "video_series"
    return "video_movie"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    hash_ = sys.argv[1]
    dry = "--dry-run" in sys.argv

    # with the temp folder enabled the torrent may still be moving
    t = wait_for_move(hash_)
    if t is None:
        log("ERROR torrent %s not found" % hash_)
        return 1
    files = json.loads(api("torrents/files?" + urllib.parse.urlencode({"hash": hash_})))

    kind = classify(t["name"], files)
    if kind is None:
        log("SKIP  '%s': no video/audio files, stays in %s" % (t["name"], t["save_path"]))
        return 0

    dest = DEST[kind]
    rename = None   # (old, new) top folder of the torrent
    if kind == "video_movie":
        root = root_folder(files)
        if root is None and len(files) == 1:
            # single-file movie: give it a "Title (Year)" folder
            dest = os.path.join(dest, movie_folder(t["name"]))
        elif root is not None and movie_folder(root) != root:
            # movie already in a folder: rename it to "Title (Year)"
            rename = (root, movie_folder(root))
    if t["save_path"].rstrip("/") == dest and rename is None:
        log("SKIP  '%s': already in %s" % (t["name"], dest))
        return 0
    if not os.path.isdir(DEST[kind]):
        log("ERROR '%s': folder %s not mounted in the container" % (t["name"], DEST[kind]))
        return 1

    final = os.path.join(dest, rename[1]) if rename else dest
    if dry:
        log("DRY   '%s' -> %s" % (t["name"], final))
        return 0

    if rename:
        api("torrents/renameFolder", {"hash": hash_, "oldPath": rename[0], "newPath": rename[1]})
    if t["save_path"].rstrip("/") != dest:
        api("torrents/setLocation", {"hashes": hash_, "location": dest})
    # wait for the move to finish, only to log it
    time.sleep(5)
    t2 = wait_for_move(hash_)
    if t2 is not None and t2["save_path"].rstrip("/") != dest:
        # setLocation answers ok even if the move then fails (e.g. permissions)
        log("ERROR '%s': move to %s failed, stays in %s (state: %s)"
            % (t["name"], dest, t2["save_path"], t2["state"]))
        return 1
    log("MOVE  '%s' -> %s (state: %s)" % (t["name"], final, t2["state"] if t2 else "removed"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never make qBittorrent fail because of this script
        log("ERROR %s: %r" % (sys.argv[1:], e))
        sys.exit(1)
