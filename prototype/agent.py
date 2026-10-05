#!/usr/bin/env python3
import argparse, json, os, subprocess, time, urllib.request

def request(url, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=10) as response: return json.loads(response.read() or b"{}")

class PcstoryAdapter:
    def __init__(self, command=None): self.progress = 0; self.command = command
    def start(self, task):
        if self.command:
            command = self.command.format(game_id=task["gameId"], force=str(task["forceUpdate"]).lower())
            print("启动 PCStory 适配器:", command)
            subprocess.Popen(command, shell=True, cwd=os.getcwd())
        else:
            print(f"[模拟] PCStory 添加任务 gameId={task['gameId']} force={task['forceUpdate']}")
        self.progress = 0
    def tick(self): self.progress = min(100, self.progress + 10); return self.progress

def main():
    p=argparse.ArgumentParser(); p.add_argument("--cafe-id",required=True); p.add_argument("--name",required=True); p.add_argument("--server",default="http://127.0.0.1:8765"); p.add_argument("--pcstory-command"); args=p.parse_args()
    base=args.server.rstrip("/"); request(base+"/api/agents/register","POST",{"cafeId":args.cafe_id,"name":args.name}); adapter=PcstoryAdapter(args.pcstory_command); print("agent online")
    while True:
        try:
            request(base+"/api/agents/heartbeat","POST",{"cafeId":args.cafe_id})
            task=request(base+"/api/tasks/next/"+args.cafe_id)
            if task.get("id"):
                adapter.start(task)
                while adapter.progress < 100:
                    latest = request(base+"/api/state")
                    current = next((item for item in latest.get("tasks", []) if item.get("id") == task["id"]), task)
                    if current.get("status") == "cancelled":
                        print("task cancelled")
                        break
                    progress=adapter.tick(); request(base+f"/api/tasks/{task['id']}/status","POST",{"status":"downloading","progress":progress}); time.sleep(1)
                if current.get("status") != "cancelled":
                    request(base+f"/api/tasks/{task['id']}/status","POST",{"status":"completed","progress":100}); print("task completed")
            time.sleep(1)
        except Exception as exc: print("agent error:", exc); time.sleep(3)

if __name__ == "__main__": main()
