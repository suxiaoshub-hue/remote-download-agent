#!/usr/bin/env python3
import json, os, tempfile, threading, time, urllib.error, urllib.request
import server

def call(path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("http://127.0.0.1:18765" + path, data=body, method=method, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request) as response: return json.loads(response.read())

server.PORT = 18765
server.DB_PATH = os.path.join(tempfile.gettempdir(), "remote_download_integration.db")
server.load_db()
from http.server import ThreadingHTTPServer
http = ThreadingHTTPServer(("127.0.0.1", 18765), server.Handler)
threading.Thread(target=http.serve_forever, daemon=True).start()
call("/api/agents/register", "POST", {"cafeId":"test-cafe", "name":"集成测试网吧"})
inventory = call("/api/agents/inventory", "POST", {
    "cafeId": "test-cafe",
    "games": [
        {"gameId": 5131, "name": "Roblox", "status": "installed", "localPath": "D:\\Games\\Roblox", "sizeBytes": 1200},
        {"gameId": 8044, "name": "CSGO", "status": "missing", "localPath": "D:\\Games\\CSGO", "sizeBytes": 5000}
    ],
    "disks": [{"path": "D:\\", "freeBytes": 9000, "totalBytes": 10000}]
})
assert inventory["cafeId"] == "test-cafe"
found = call("/api/cafes/test-cafe/inventory?query=rob")
assert found["games"][0]["gameId"] == 5131
assert found["games"][0]["status"] == "installed"
assert found["disks"][0]["freeBytes"] == 9000
missing = call("/api/cafes/test-cafe/inventory?query=csgo")
assert missing["games"][0]["status"] == "missing"
not_downloaded = call("/api/cafes/test-cafe/inventory?query=dota")
assert not_downloaded["games"][0]["status"] == "not_installed"
try:
    call("/api/agents/inventory", "POST", {"cafeId":"test-cafe", "games":[{"gameId":1,"status":"bad"}], "disks":[]})
    raise AssertionError("invalid inventory was accepted")
except urllib.error.HTTPError as error:
    assert error.code == 400
    assert json.loads(error.read())["error"] == "invalid inventory game status"
task = call("/api/tasks", "POST", {"cafeId":"test-cafe", "gameId":8263})
accepted = call("/api/tasks/next/test-cafe")
assert accepted["id"] == task["id"]
call("/api/tasks/%s/status" % task["id"], "POST", {"status":"completed", "progress":100})
state = call("/api/state")
assert next(item for item in state["tasks"] if item["id"] == task["id"])["status"] == "completed"
http.shutdown()
print("integration test passed")
