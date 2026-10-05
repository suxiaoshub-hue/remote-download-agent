#!/usr/bin/env python3
import argparse, json, os, sys

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="agent-config.json")
args = parser.parse_args()
with open(args.config, encoding="utf-8") as file:
    config = json.load(file)
sys.path.insert(0, os.path.dirname(__file__))
import agent
sys.argv = ["Agent.exe", "--cafe-id", config["cafeId"], "--name", config["name"], "--server", config["server"]]
if config.get("pcstoryCommand"):
    sys.argv.extend(["--pcstory-command", config["pcstoryCommand"]])
agent.main()
