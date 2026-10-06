#!/usr/bin/env python3
import argparse, json, ntpath, os, shutil, subprocess, time, urllib.request

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

def _value(item, *names, default=None):
    for name in names:
        if name in item: return item[name]
    return default

def collect_inventory(inventory_file=None, disk_paths=None):
    games = []
    if inventory_file:
        with open(inventory_file, encoding="utf-8-sig") as file:
            source = json.load(file)
        source = source.get("games", source) if isinstance(source, dict) else source
        for item in source:
            game_id = int(_value(item, "gameId", "GID"))
            path = str(_value(item, "localPath", "LocalPath", default="") or "")
            explicit = _value(item, "status", default=None)
            if explicit in ("installed", "missing", "downloading", "unknown"):
                status = explicit
            elif path:
                status = "installed" if os.path.exists(path) else "missing"
            else:
                status = "unknown"
            games.append({
                "gameId": game_id,
                "name": str(_value(item, "name", "Name", default=game_id)),
                "status": status,
                "localPath": path,
                "localVersion": int(_value(item, "localVersion", "LocalVersion", default=0) or 0),
                "serverVersion": int(_value(item, "serverVersion", "ServerVersion", default=0) or 0),
                "sizeBytes": max(0, int(_value(item, "sizeBytes", "SizeBytes", default=0) or 0)),
            })
    roots = list(disk_paths or [])
    for game in games:
        drive, _ = ntpath.splitdrive(game["localPath"])
        if drive and drive + "\\" not in roots:
            roots.append(drive + "\\")
    disks = []
    for path in roots:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        disks.append({"path": path, "freeBytes": usage.free, "totalBytes": usage.total})
    return {"games": games, "disks": disks}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--cafe-id",required=True); p.add_argument("--name",required=True); p.add_argument("--server",default="http://127.0.0.1:8765"); p.add_argument("--pcstory-command"); p.add_argument("--inventory-file"); p.add_argument("--disk-path",action="append",default=[]); p.add_argument("--inventory-interval",type=int,default=30); args=p.parse_args()
    base=args.server.rstrip("/"); request(base+"/api/agents/register","POST",{"cafeId":args.cafe_id,"name":args.name}); adapter=PcstoryAdapter(args.pcstory_command); print("agent online")
    last_inventory = 0
    while True:
        try:
            request(base+"/api/agents/heartbeat","POST",{"cafeId":args.cafe_id})
            if time.time() - last_inventory >= max(5, args.inventory_interval):
                snapshot = collect_inventory(args.inventory_file, args.disk_path)
                request(base+"/api/agents/inventory","POST",dict(snapshot, cafeId=args.cafe_id))
                last_inventory = time.time()
                print("inventory reported: %d games, %d disks" % (len(snapshot["games"]), len(snapshot["disks"])))
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
