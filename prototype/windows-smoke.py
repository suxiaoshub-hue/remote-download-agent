import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    root = Path('dist').resolve()
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        for name in ('Server.exe', 'Agent.exe'):
            shutil.copy(root / name, folder / name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        secret = 'smoke-admin-secret-at-least-sixteen'
        (folder / 'server-config.json').write_text(json.dumps({'host': '127.0.0.1', 'port': port, 'adminToken': secret}), encoding='utf-8')
        base = 'http://127.0.0.1:' + str(port)

        def request(path, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(base + path, data=data, headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=2) as response:
                return json.loads(response.read())

        server = subprocess.Popen([str(folder / 'Server.exe')], cwd=folder)
        agent = None
        try:
            deadline = time.time() + 60
            while True:
                try:
                    request('/api/state')
                    break
                except (OSError, urllib.error.URLError):
                    if server.poll() is not None or time.time() > deadline:
                        raise RuntimeError('Packaged Server did not start')
                    time.sleep(.25)
            with urllib.request.urlopen(base + '/') as response:
                assert '网吧远程下载'.encode() in response.read(), 'Packaged webpage missing'
            created = request('/api/cafes', {'name': '打包测试', 'server': base})
            config = created['config']
            config['pcstoryFolder'] = str(folder / 'no-pcstory')
            (folder / 'agent-config.json').write_text(json.dumps(config), encoding='utf-8')
            agent = subprocess.Popen([str(folder / 'Agent.exe')], cwd=folder)
            deadline = time.time() + 60
            while True:
                cafe = request('/api/state')['cafes'][0]
                if cafe['online'] and cafe['error']:
                    break
                if agent.poll() is not None or time.time() > deadline:
                    raise RuntimeError('Packaged Agent did not report heartbeat/inventory error')
                time.sleep(.25)
            assert (folder / 'agent.log').exists()
            assert 'tokenHash' not in cafe and 'agentToken' not in cafe
            print('PASS frozen Server webpage, auth, config, Agent heartbeat, inventory errors and local log')
        finally:
            for process in (agent, server):
                if process is not None and process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, check=False)
                    process.wait(timeout=15)


if __name__ == '__main__':
    main()
