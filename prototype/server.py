#!/usr/bin/env python3
import json
import sqlite3
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8765
lock = threading.RLock()
cafes = {}
tasks = {}
inventories = {}
DB_PATH = "remote_download.db"
GAME_CATALOG = [
    {"gameId": 8263, "name": "Dota2", "sizeBytes": 0},
    {"gameId": 8044, "name": "CSGO", "sizeBytes": 0},
    {"gameId": 8574, "name": "测试游戏", "sizeBytes": 0},
    {"gameId": 5131, "name": "Roblox", "sizeBytes": 0},
]

def load_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cafes (id TEXT PRIMARY KEY, name TEXT NOT NULL, online INTEGER NOT NULL, lastSeen REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, cafeId TEXT NOT NULL, gameId INTEGER NOT NULL, downloader TEXT NOT NULL, forceUpdate INTEGER NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL, updatedAt REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS inventory_games (cafeId TEXT NOT NULL, gameId INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, localPath TEXT NOT NULL, localVersion INTEGER NOT NULL, serverVersion INTEGER NOT NULL, sizeBytes INTEGER NOT NULL, updatedAt REAL NOT NULL, PRIMARY KEY(cafeId, gameId))")
        db.execute("CREATE TABLE IF NOT EXISTS inventory_disks (cafeId TEXT NOT NULL, path TEXT NOT NULL, freeBytes INTEGER NOT NULL, totalBytes INTEGER NOT NULL, updatedAt REAL NOT NULL, PRIMARY KEY(cafeId, path))")
        cafes.update({row[0]: {"id": row[0], "name": row[1], "online": False, "lastSeen": row[3]} for row in db.execute("SELECT id,name,online,lastSeen FROM cafes")})
        tasks.update({row[0]: {"id": row[0], "cafeId": row[1], "gameId": row[2], "downloader": row[3], "forceUpdate": bool(row[4]), "status": row[5], "progress": row[6], "updatedAt": row[7]} for row in db.execute("SELECT id,cafeId,gameId,downloader,forceUpdate,status,progress,updatedAt FROM tasks")})
        for row in db.execute("SELECT cafeId,gameId,name,status,localPath,localVersion,serverVersion,sizeBytes,updatedAt FROM inventory_games"):
            inventories.setdefault(row[0], {"cafeId": row[0], "games": {}, "disks": [], "updatedAt": row[8]})["games"][row[1]] = {"gameId": row[1], "name": row[2], "status": row[3], "localPath": row[4], "localVersion": row[5], "serverVersion": row[6], "sizeBytes": row[7], "updatedAt": row[8]}
        for row in db.execute("SELECT cafeId,path,freeBytes,totalBytes,updatedAt FROM inventory_disks"):
            inventory = inventories.setdefault(row[0], {"cafeId": row[0], "games": {}, "disks": [], "updatedAt": row[4]})
            inventory["disks"].append({"path": row[1], "freeBytes": row[2], "totalBytes": row[3], "updatedAt": row[4]})

def save_cafe(cafe):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR REPLACE INTO cafes VALUES (?,?,?,?)", (cafe["id"], cafe["name"], int(cafe["online"]), cafe["lastSeen"]))

def save_task(task):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?)", (task["id"], task["cafeId"], task["gameId"], task["downloader"], int(task["forceUpdate"]), task["status"], task["progress"], task["updatedAt"]))

def save_inventory(inventory):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM inventory_games WHERE cafeId=?", (inventory["cafeId"],))
        db.execute("DELETE FROM inventory_disks WHERE cafeId=?", (inventory["cafeId"],))
        for game in inventory["games"].values():
            db.execute("INSERT INTO inventory_games VALUES (?,?,?,?,?,?,?,?,?)", (inventory["cafeId"], game["gameId"], game["name"], game["status"], game["localPath"], game["localVersion"], game["serverVersion"], game["sizeBytes"], game["updatedAt"]))
        for disk in inventory["disks"]:
            db.execute("INSERT INTO inventory_disks VALUES (?,?,?,?,?)", (inventory["cafeId"], disk["path"], disk["freeBytes"], disk["totalBytes"], disk["updatedAt"]))

def clean_inventory(data):
    now = time.time()
    games = {}
    for raw in data.get("games", []):
        game_id = int(raw["gameId"])
        status = str(raw.get("status", "unknown"))
        if status not in ("installed", "missing", "not_installed", "downloading", "unknown"):
            raise ValueError("invalid inventory game status")
        games[game_id] = {"gameId": game_id, "name": str(raw.get("name", game_id)), "status": status, "localPath": str(raw.get("localPath", "")), "localVersion": int(raw.get("localVersion", 0)), "serverVersion": int(raw.get("serverVersion", 0)), "sizeBytes": max(0, int(raw.get("sizeBytes", 0))), "updatedAt": now}
    disks = []
    for raw in data.get("disks", []):
        total = max(0, int(raw["totalBytes"]))
        free = max(0, min(int(raw["freeBytes"]), total))
        disks.append({"path": str(raw["path"]), "freeBytes": free, "totalBytes": total, "updatedAt": now})
    return {"cafeId": str(data["cafeId"]), "games": games, "disks": disks, "updatedAt": now}

INDEX = """<!doctype html><meta charset=utf-8><title>远程下载后台</title>
<h1>远程下载后台</h1><div id=app></div>
<script>
async function api(path, options) { const r=await fetch(path, options); return r.json(); }
async function refresh() { const selectedCafe=document.querySelector('#cafe')?.value||''; const selectedQuery=document.querySelector('#query')?.value||''; const [d,games]=await Promise.all([api('/api/state'),api('/api/games')]); document.querySelector('#app').innerHTML=`
<p>网吧：</p><select id="cafe" onchange="searchInventory()">${d.cafes.map(c=>`<option value="${c.id}">${c.name} (${c.online?'在线':'离线'})</option>`).join('')}</select> <button onclick="renameCafe(document.querySelector('#cafe').value)">改名</button>
<input id="query" placeholder="搜索游戏名称或 GID" oninput="searchInventory()"><div id="inventory"></div>
<p>任务：</p><table border=1><tr><th>网吧</th><th>游戏</th><th>状态</th><th>进度</th><th>操作</th></tr>${d.tasks.map(t=>`<tr><td>${t.cafeId}</td><td>${t.gameId}</td><td>${t.status}</td><td>${t.progress}%</td><td>${['queued','accepted','downloading'].includes(t.status)?`<button onclick="cancelTask('${t.id}')">取消</button>`:''}</td></tr>`).join('')}</table>
<p>游戏：${games.map(g=>`${g.gameId} ${g.name}`).join('，')}</p>`; const cafe=document.querySelector('#cafe'); const query=document.querySelector('#query'); if(selectedCafe && [...cafe.options].some(option=>option.value===selectedCafe)) cafe.value=selectedCafe; query.value=selectedQuery; await searchInventory(); }
async function searchInventory() { const cafe=document.querySelector('#cafe'); const query=document.querySelector('#query'); if(!cafe||!query) return; const data=await api('/api/cafes/'+encodeURIComponent(cafe.value)+'/inventory?query='+encodeURIComponent(query.value)); document.querySelector('#inventory').innerHTML=`<p>磁盘：${data.disks.map(d=>`${d.path} 可用 ${formatBytes(d.freeBytes)} / ${formatBytes(d.totalBytes)}`).join('；')||'未上报'}</p><table border=1><tr><th>GID</th><th>名称</th><th>状态</th><th>本地路径</th><th>操作</th></tr>${data.games.map(g=>`<tr><td>${g.gameId}</td><td>${g.name}</td><td>${statusText(g.status)}</td><td>${g.localPath||''}</td><td>${g.status==='installed'?'已下载':`<button onclick="download('${cafe.value}',${g.gameId})">下发下载</button>`}</td></tr>`).join('')}</table>`; }
function formatBytes(value) { const units=['B','KB','MB','GB','TB']; let n=value, i=0; while(n>=1024&&i<units.length-1){n/=1024;i++;} return n.toFixed(i?1:0)+' '+units[i]; }
function statusText(value) { return {installed:'已下载',missing:'文件缺失',not_installed:'未下载',downloading:'下载中',unknown:'未知'}[value]||value; }
async function renameCafe(cafeId) { const name=prompt('输入新的网吧名称'); if(name) { await api('/api/cafes/'+cafeId+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}); refresh(); } }
async function cancelTask(taskId) { await api('/api/tasks/'+taskId+'/cancel',{method:'POST'}); refresh(); }
async function download(cafeId,gameId) { await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cafeId,gameId,downloader:'Pcstory',forceUpdate:false})}); refresh(); }
 refresh(); setInterval(refresh,2000);
</script>"""

class Handler(BaseHTTPRequestHandler):
    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/":
            body = INDEX.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            with lock:
                now = time.time()
                for cafe in cafes.values(): cafe["online"] = now - cafe["lastSeen"] < 15
                self._json({"cafes": list(cafes.values()), "tasks": list(tasks.values())})
            return
        if parsed.path == "/api/games":
            self._json(GAME_CATALOG); return
        if parsed.path.startswith("/api/cafes/") and parsed.path.endswith("/inventory"):
            cafe_id = parsed.path.split("/")[3]
            query = parse_qs(parsed.query).get("query", [""])[0].strip().lower()
            with lock:
                inventory = inventories.get(cafe_id, {"cafeId": cafe_id, "games": {}, "disks": [], "updatedAt": 0})
                games = list(inventory["games"].values()) if cafe_id in inventories else []
                if cafe_id in inventories:
                    games_by_id = {game["gameId"]: dict(game, status="not_installed") for game in GAME_CATALOG}
                    games_by_id.update(inventory["games"])
                    games = list(games_by_id.values())
                if query: games = [game for game in games if query in str(game["gameId"]) or query in game["name"].lower()]
                self._json({"cafeId": cafe_id, "reported": cafe_id in inventories, "games": sorted(games, key=lambda game: (game["name"], game["gameId"])), "disks": inventory["disks"], "updatedAt": inventory["updatedAt"]})
            return
        if parsed.path.startswith("/api/tasks/next/"):
            cafe_id = parsed.path.rsplit("/", 1)[-1]
            with lock:
                pending = next((t for t in tasks.values() if t["cafeId"] == cafe_id and t["status"] == "queued"), None)
                if pending: pending["status"] = "accepted"; pending["updatedAt"] = time.time()
                self._json(pending or {})
            return
        self._json({"error":"not found"}, 404)
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0)); data = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/agents/register":
            cafe = {"id": data["cafeId"], "name": data.get("name", data["cafeId"]), "online": True, "lastSeen": time.time()}
            with lock: cafes[cafe["id"]] = cafe
            save_cafe(cafe)
            self._json(cafe); return
        if self.path == "/api/agents/inventory":
            try:
                inventory = clean_inventory(data)
            except (KeyError, TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400); return
            with lock:
                if inventory["cafeId"] not in cafes: self._json({"error":"cafe not registered"}, 404); return
                inventories[inventory["cafeId"]] = inventory
                cafes[inventory["cafeId"]].update(online=True, lastSeen=time.time())
                save_inventory(inventory); save_cafe(cafes[inventory["cafeId"]])
            self._json({"cafeId": inventory["cafeId"], "gameCount": len(inventory["games"]), "diskCount": len(inventory["disks"]), "updatedAt": inventory["updatedAt"]}); return
        if self.path == "/api/tasks":
            task = {"id": uuid.uuid4().hex, "cafeId": data["cafeId"], "gameId": int(data["gameId"]), "downloader": data.get("downloader", "Pcstory"), "forceUpdate": bool(data.get("forceUpdate", False)), "status":"queued", "progress":0, "updatedAt":time.time()}
            with lock: tasks[task["id"]] = task
            save_task(task)
            self._json(task, 201); return
        if self.path.startswith("/api/cafes/") and self.path.endswith("/rename"):
            cafe_id = self.path.split("/")[3]
            with lock:
                if cafe_id not in cafes: self._json({"error":"cafe not found"}, 404); return
                cafes[cafe_id]["name"] = str(data.get("name", "")).strip() or cafes[cafe_id]["name"]
                save_cafe(cafes[cafe_id]); self._json(cafes[cafe_id]); return
        if self.path == "/api/agents/heartbeat":
            with lock:
                if data.get("cafeId") in cafes: cafes[data["cafeId"]].update(online=True, lastSeen=time.time())
                if data.get("cafeId") in cafes: save_cafe(cafes[data["cafeId"]])
            self._json({"ok": True}); return
        if self.path.startswith("/api/tasks/") and self.path.endswith("/status"):
            task_id = self.path.split("/")[3]
            with lock:
                if task_id in tasks: tasks[task_id].update({k:data[k] for k in ("status","progress") if k in data}, updatedAt=time.time())
                if task_id in tasks: save_task(tasks[task_id])
                self._json(tasks.get(task_id, {})); return
        if self.path.startswith("/api/tasks/") and self.path.endswith("/cancel"):
            task_id = self.path.split("/")[3]
            with lock:
                if task_id in tasks and tasks[task_id]["status"] in ("queued", "accepted", "downloading"):
                    tasks[task_id].update(status="cancelled", updatedAt=time.time())
                    save_task(tasks[task_id])
                self._json(tasks.get(task_id, {})); return
        self._json({"error":"not found"}, 404)

if __name__ == "__main__":
    load_db()
    print(f"server listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
