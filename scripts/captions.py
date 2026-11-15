# Live captions from the board: python scripts/captions.py BOARD_IP [PORT]
import json
import socket
import sys

port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
with socket.create_connection((sys.argv[1], port)) as connection:
    for line in connection.makefile(encoding="utf-8"):
        event = json.loads(line)
        if event["final"]:
            print("\r\033[K" + event["text"], flush=True)
        else:
            print(f"\r\033[K{event['text']} \033[33m{event['tentative']}\033[0m", end="", flush=True)
