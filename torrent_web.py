#!/usr/bin/env python3
"""
torrent_web.py - Small LAN web page to search torrents with qBittorrent

Paste one or more searches (one per line), the page runs them through qBittorrent's
search engine and shows the results; one click adds them to the downloads.

Uses only the Python standard library (no extra pip/apt packages).

Start:
    python3 torrent_web.py
    then open from another device:  http://SERVER-IP:8083

Configuration (environment variables or the CONFIG section):
    QBIT_URL      qBittorrent Web UI address              (default http://localhost:8082)
    QBIT_USER     Web UI user
    QBIT_PASS     Web UI password
    WEB_HOST      address to listen on                    (default 0.0.0.0)
    WEB_PORT      port of the page                        (default 8083)
    WEB_PASSWORD  if set, the page asks for user/password (any user name)
"""

import base64
import hmac
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- CONFIG -----------------------------
QBIT_URL = os.getenv("QBIT_URL", "http://localhost:8082").rstrip("/")
QBIT_USER = os.getenv("QBIT_USER", "")
QBIT_PASS = os.getenv("QBIT_PASS", "")

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8083"))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")   # empty = no password

SEARCH_TIMEOUT = 60
# ------------------------------------------------------------------


class QbitError(Exception):
    pass


class QBittorrent:
    """Minimal client for the qBittorrent Web API (standard library only)."""

    def __init__(self, url, user, password):
        self.url, self.user, self.password = url, user, password
        self.lock = threading.Lock()
        self._new_session()

    def _new_session(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener.addheaders = [("Referer", self.url), ("Origin", self.url)]
        self.logged_in = False

    def _request(self, path, data=None, params=None, timeout=30):
        url = f"{self.url}/api/v2/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self.opener.open(url, data=body, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            raise QbitError(f"qBittorrent not reachable at {self.url} ({e.reason}). "
                            "Is it running with the Web UI enabled?")

    def login(self):
        with self.lock:
            if self.logged_in:
                return
            status, text = self._request("auth/login",
                                         {"username": self.user, "password": self.password})
            if text.strip() == "Ok.":
                self.logged_in = True
                return
            # login ok without "Ok." body (qBittorrent 5.2+) or localhost auth bypass
            if self._request("app/version")[0] == 200:
                self.logged_in = True
                return
            if status == 403:
                raise QbitError("qBittorrent blocked access after too many failed attempts: "
                                "restart it or wait.")
            raise QbitError("qBittorrent login failed: check QBIT_USER and QBIT_PASS.")

    def api(self, path, data=None, params=None, timeout=30):
        self.login()
        status, text = self._request(path, data, params, timeout)
        if status == 403:              # session expired: log in again once
            self.logged_in = False
            self.login()
            status, text = self._request(path, data, params, timeout)
        if status >= 400:
            raise QbitError(f"qBittorrent error {status} on {path}: {text.strip()[:200]}")
        return text

    def enabled_plugins(self):
        return [p["name"] for p in json.loads(self.api("search/plugins")) if p.get("enabled")]

    def search(self, text, category="all"):
        job = json.loads(self.api("search/start", {"pattern": text, "plugins": "enabled",
                                                   "category": category}))["id"]
        try:
            start = time.time()
            while time.time() - start < SEARCH_TIMEOUT:
                status = json.loads(self.api("search/status", params={"id": job}))
                if status and status[0].get("status") == "Stopped":
                    break
                time.sleep(1)
            data = json.loads(self.api("search/results", params={"id": job, "limit": 0}))
        finally:
            for p in ("search/stop", "search/delete"):
                try:
                    self.api(p, {"id": job})
                except QbitError:
                    pass

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("fileName", ""),
                "seeders": max(int(r.get("nbSeeders") or 0), 0),
                "leechers": max(int(r.get("nbLeechers") or 0), 0),
                "mb": round(max(int(r.get("fileSize") or 0), 0) / 1048576, 1),
                "site": r.get("siteUrl", ""),
                "page": r.get("descrLink", ""),
                "link": r.get("fileUrl", ""),
            })
        results.sort(key=lambda x: x["seeders"], reverse=True)
        return results

    def add(self, link):
        self.api("torrents/add", {"urls": link})


QB = QBittorrent(QBIT_URL, QBIT_USER, QBIT_PASS)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Torrent search</title>
<style>
  :root {
    --bg: #f4f2ee; --card: #ffffff; --ink: #1d1c1a; --muted: #6d6a64; --line: #e3dfd8;
    --accent: #2f6f5e; --accent-ink: #ffffff; --warn: #a2462c; --chip: #ece8e1;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #151514; --card: #1f1f1d; --ink: #ecebe7; --muted: #9b978f; --line: #33322f;
            --accent: #6cbfa6; --accent-ink: #0f1513; --warn: #e48a6d; --chip: #2a2a27; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -.01em; }
  .sub { color: var(--muted); margin: 0 0 20px; font-size: 14px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
  textarea { width: 100%; min-height: 120px; resize: vertical; padding: 10px 12px; border-radius: 8px;
             border: 1px solid var(--line); background: var(--bg); color: var(--ink); font: inherit; }
  .filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 12px 0; }
  label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
  input, select { padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line);
                  background: var(--bg); color: var(--ink); font: inherit; }
  .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  .check { flex-direction: row; align-items: center; gap: 8px; color: var(--ink); font-size: 14px; }
  button { cursor: pointer; border: 0; border-radius: 8px; font: inherit; font-weight: 600; }
  .primary { background: var(--accent); color: var(--accent-ink); padding: 10px 20px; }
  .primary:disabled { opacity: .5; cursor: default; }
  .status { font-size: 13px; color: var(--muted); margin: 12px 2px 0; min-height: 1.4em; }
  .status.err { color: var(--warn); }
  section.group { margin-top: 22px; }
  section.group h2 { font-size: 16px; margin: 0 0 8px; display: flex; gap: 8px; align-items: baseline; }
  section.group h2 small { color: var(--muted); font-weight: 400; font-size: 13px; }
  .result { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 10px 12px;
            border-top: 1px solid var(--line); align-items: center; }
  .result:first-of-type { border-top: 0; }
  .title { word-break: break-word; }
  .meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; color: var(--muted); }
  .chip { background: var(--chip); border-radius: 999px; padding: 1px 8px; }
  .chip.seed { color: var(--accent); font-weight: 600; }
  .meta a { color: var(--muted); }
  .download { grid-row: 1 / span 2; grid-column: 2; background: var(--chip); color: var(--ink); padding: 8px 14px; }
  .download.ok { background: var(--accent); color: var(--accent-ink); }
  .download.err { color: var(--warn); }
  .empty { color: var(--muted); padding: 10px 12px; font-size: 14px; }
</style>
</head>
<body>
<main>
  <h1>Torrent search</h1>
  <p class="sub" id="plugins">Connecting to qBittorrent…</p>

  <div class="card">
    <textarea id="queries" placeholder="One search per line&#10;e.g. debian 12 netinst"></textarea>
    <div class="filters">
      <label>Type
        <select id="type">
          <option value="all">All</option><option value="movies">Movies</option>
          <option value="tv">TV shows</option><option value="music">Music</option>
          <option value="games">Games</option><option value="anime">Anime</option>
          <option value="software">Software</option><option value="books">Books</option>
        </select>
      </label>
      <label>Results per search <input id="top" type="number" min="1" max="50" value="5"></label>
      <label>Minimum seeders <input id="seed" type="number" min="0" value="1"></label>
      <label>Must contain <input id="filter" placeholder="e.g. 1080p"></label>
      <label>Max MB <input id="maxmb" type="number" min="0" placeholder="no limit"></label>
    </div>
    <div class="row">
      <label class="check"><input type="checkbox" id="auto"> Download the best result of each search right away</label>
      <button class="primary" id="go">Search</button>
    </div>
    <div class="status" id="status"></div>
  </div>

  <div id="results"></div>
</main>

<script>
const $ = id => document.getElementById(id);

async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({error: "Invalid response from the server"}));
  if (!r.ok || data.error) throw new Error(data.error || ("Error " + r.status));
  return data;
}

function status(msg, error) { const s = $("status"); s.textContent = msg; s.className = "status" + (error ? " err" : ""); }

function safeLink(u) { return /^https?:\/\//i.test(u || "") ? u : null; }

async function download(btn, link) {
  btn.disabled = true; btn.textContent = "…";
  try { await api("/api/add", {link}); btn.textContent = "Added"; btn.className = "download ok"; }
  catch (e) { btn.textContent = "Error"; btn.className = "download err"; btn.title = e.message; btn.disabled = false; }
}

function showGroup(query, results) {
  const sec = document.createElement("section"); sec.className = "group";
  const h = document.createElement("h2"); h.textContent = query;
  const n = document.createElement("small"); n.textContent = results.length + " results";
  h.append(" ", n); sec.append(h);
  const card = document.createElement("div"); card.className = "card"; card.style.padding = "0";
  if (!results.length) {
    const v = document.createElement("div"); v.className = "empty"; v.textContent = "No results with these filters.";
    card.append(v);
  }
  const buttons = [];
  for (const r of results) {
    const row = document.createElement("div"); row.className = "result";
    const t = document.createElement("div"); t.className = "title"; t.textContent = r.title;
    const meta = document.createElement("div"); meta.className = "meta";
    const chip = (txt, cls) => { const c = document.createElement("span"); c.className = "chip " + (cls || ""); c.textContent = txt; return c; };
    meta.append(chip(r.seeders + " seed", "seed"), chip(r.leechers + " leech"), chip(r.mb ? r.mb + " MB" : "? MB"));
    const page = safeLink(r.page);
    if (page) { const a = document.createElement("a"); a.href = page; a.target = "_blank"; a.rel = "noopener noreferrer";
                a.textContent = new URL(page).hostname; meta.append(a); }
    const b = document.createElement("button"); b.className = "download"; b.textContent = "Download";
    b.onclick = () => download(b, r.link);
    row.append(t, b, meta); card.append(row); buttons.push(b);
  }
  sec.append(card); $("results").append(sec);
  return buttons;
}

$("go").onclick = async () => {
  const queries = $("queries").value.split("\n").map(s => s.trim()).filter(s => s && !s.startsWith("#"));
  if (!queries.length) { status("Enter at least one search.", true); return; }
  $("go").disabled = true; $("results").innerHTML = "";
  const filter = $("filter").value.trim().toLowerCase();
  const maxmb = parseFloat($("maxmb").value) || 0;
  const minSeed = parseInt($("seed").value) || 0;
  const top = parseInt($("top").value) || 5;
  try {
    for (let i = 0; i < queries.length; i++) {
      const q = queries[i];
      status(`Search ${i + 1} of ${queries.length}: ${q}…`);
      let res;
      try { res = (await api("/api/search", {query: q, type: $("type").value})).results; }
      catch (e) { status(`Error on "${q}": ${e.message}`, true); continue; }
      res = res.filter(r => r.seeders >= minSeed)
               .filter(r => !filter || r.title.toLowerCase().includes(filter))
               .filter(r => !maxmb || (r.mb && r.mb <= maxmb))
               .slice(0, top);
      const buttons = showGroup(q, res);
      if ($("auto").checked && buttons.length) await download(buttons[0], res[0].link);
    }
    if (!$("status").classList.contains("err")) status("Done.");
  } finally { $("go").disabled = false; }
};

api("/api/plugins").then(d => {
  $("plugins").textContent = d.plugins.length ? "Enabled plugins: " + d.plugins.join(", ")
    : "No search plugin enabled in qBittorrent.";
}).catch(e => { $("plugins").textContent = e.message; $("plugins").style.color = "var(--warn)"; });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "TorrentWeb/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def _authorized(self):
        if not WEB_PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pwd = base64.b64decode(h[6:]).decode().partition(":")
                if hmac.compare_digest(pwd.encode(), WEB_PASSWORD.encode()):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Torrent search"')
        self.end_headers()
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        # blocks forms from other sites (CSRF): a form cannot send application/json
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._authorized():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/plugins":
            try:
                return self._send(200, {"plugins": QB.enabled_plugins()})
            except QbitError as e:
                return self._send(502, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        try:
            path = urllib.parse.urlsplit(self.path).path
            data = self._json_body()
            if path == "/api/search":
                q = str(data.get("query", "")).strip()
                if not q:
                    return self._send(400, {"error": "empty search"})
                return self._send(200, {"results": QB.search(q, str(data.get("type") or "all"))})
            if path == "/api/add":
                link = str(data.get("link", "")).strip()
                if not link.startswith(("magnet:", "http://", "https://")):
                    return self._send(400, {"error": "invalid link"})
                QB.add(link)
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except QbitError as e:
            self._send(502, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, {"error": f"invalid request: {e}"})


def main():
    srv = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    print(f"Page running at http://{WEB_HOST}:{WEB_PORT}  (qBittorrent: {QBIT_URL})")
    if not WEB_PASSWORD:
        print("Warning: no password set, anyone on the LAN can use the page (WEB_PASSWORD).")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nClosed.")


if __name__ == "__main__":
    main()
