#!/usr/bin/env python3
import json
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8765
lock = threading.RLock()
cafes = {}
tasks = {}
DB_PATH = "remote_download.db"

def load_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cafes (id TEXT PRIMARY KEY, name TEXT NOT NULL, online INTEGER NOT NULL, lastSeen REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, cafeId TEXT NOT NULL, gameId INTEGER NOT NULL, downloader TEXT NOT NULL, forceUpdate INTEGER NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL, updatedAt REAL NOT NULL)")
        cafes.update({row[0]: {"id": row[0], "name": row[1], "online": False, "lastSeen": row[3]} for row in db.execute("SELECT id,name,online,lastSeen FROM cafes")})
        tasks.update({row[0]: {"id": row[0], "cafeId": row[1], "gameId": row[2], "downloader": row[3], "forceUpdate": bool(row[4]), "status": row[5], "progress": row[6], "updatedAt": row[7]} for row in db.execute("SELECT id,cafeId,gameId,downloader,forceUpdate,status,progress,updatedAt FROM tasks")})

def save_cafe(cafe):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR REPLACE INTO cafes VALUES (?,?,?,?)", (cafe["id"], cafe["name"], int(cafe["online"]), cafe["lastSeen"]))

def save_task(task):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?)", (task["id"], task["cafeId"], task["gameId"], task["downloader"], int(task["forceUpdate"]), task["status"], task["progress"], task["updatedAt"]))

INDEX = """<!doctype html><meta charset=utf-8><title>远程下载后台</title>
<h1>远程下载后台</h1><div id=app></div>
<script>
async function api(path, options) { const r=await fetch(path, options); return r.json(); }
async function refresh() { const [d,games]=await Promise.all([api('/api/state'),api('/api/games')]); document.querySelector('#app').innerHTML=`
<p>网吧：</p><ul>${d.cafes.map(c=>`<li>${c.name} (${c.id}) - ${c.online?'在线':'离线'} <button onclick="renameCafe('${c.id}')">改名</button> <button onclick="download('${c.id}')">下发测试下载</button></li>`).join('')}</ul>
<p>任务：</p><table border=1><tr><th>网吧</th><th>游戏</th><th>状态</th><th>进度</th><th>操作</th></tr>${d.tasks.map(t=>`<tr><td>${t.cafeId}</td><td>${t.gameId}</td><td>${t.status}</td><td>${t.progress}%</td><td>${['queued','accepted','downloading'].includes(t.status)?`<button onclick="cancelTask('${t.id}')">取消</button>`:''}</td></tr>`).join('')}</table>
<p>游戏：${games.map(g=>`${g.gameId} ${g.name}`).join('，')}</p>`; }
async function renameCafe(cafeId) { const name=prompt('输入新的网吧名称'); if(name) { await api('/api/cafes/'+cafeId+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}); refresh(); } }
async function cancelTask(taskId) { await api('/api/tasks/'+taskId+'/cancel',{method:'POST'}); refresh(); }
async function download(cafeId) { await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cafeId,gameId:8263,downloader:'Pcstory',forceUpdate:false})}); refresh(); }
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
        if self.path == "/api/state":
            with lock:
                now = time.time()
                for cafe in cafes.values(): cafe["online"] = now - cafe["lastSeen"] < 15
                self._json({"cafes": list(cafes.values()), "tasks": list(tasks.values())})
            return
        if self.path == "/api/games":
            self._json([{"gameId": 8263, "name": "Dota2"}, {"gameId": 8044, "name": "CSGO"}, {"gameId": 8574, "name": "测试游戏"}]); return
        if self.path.startswith("/api/tasks/next/"):
            cafe_id = self.path.rsplit("/", 1)[-1]
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
