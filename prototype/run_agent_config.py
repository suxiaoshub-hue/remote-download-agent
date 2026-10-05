#!/usr/bin/env python3
import argparse, json, os, sys

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="agent-config.json")
args = parser.parse_args()
with open(args.config, encoding="utf-8") as file:
    config = json.load(file)
command = [sys.executable, os.path.join(os.path.dirname(__file__), "agent.py"), "--cafe-id", config["cafeId"], "--name", config["name"], "--server", config["server"]]
if config.get("pcstoryCommand"):
    command.extend(["--pcstory-command", config["pcstoryCommand"]])
os.execv(command[0], command)
