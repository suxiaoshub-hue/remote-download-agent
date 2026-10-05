#!/usr/bin/env python3
import json, os, tempfile, threading, time, urllib.request
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
task = call("/api/tasks", "POST", {"cafeId":"test-cafe", "gameId":8263})
accepted = call("/api/tasks/next/test-cafe")
assert accepted["id"] == task["id"]
call("/api/tasks/%s/status" % task["id"], "POST", {"status":"completed", "progress":100})
state = call("/api/state")
assert next(item for item in state["tasks"] if item["id"] == task["id"])["status"] == "completed"
http.shutdown()
print("integration test passed")
