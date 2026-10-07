import json
import subprocess
import time
import urllib.error
import urllib.request


def command(*arguments):
    return subprocess.check_output(['docker', *arguments], text=True).strip()


def main():
    container = None
    volume = command('volume', 'create')
    token = 'linux-container-test-secret'
    try:
        container = command('run', '-d', '-p', '127.0.0.1::8765', '-e', 'REMOTE_ADMIN_TOKEN=' + token,
                            '-v', volume + ':/data', 'remote-download-server:test')
        port = command('port', container, '8765/tcp').rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port

        def request(path, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(base + path, data=body,
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.loads(response.read())

        def ready():
            deadline = time.monotonic() + 30
            while True:
                try:
                    return request('/api/state')
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.2)

        ready()
        with urllib.request.urlopen(base + '/') as response:
            assert '网吧远程下载'.encode() in response.read()
        cafe = request('/api/cafes', {'name': 'Linux 容器网吧', 'server': 'https://test.example.com'})
        command('restart', container)
        restarted_port = command('port', container, '8765/tcp').rsplit(':', 1)[1]
        print('Published port before/after restart:', port, restarted_port)
        base = 'http://127.0.0.1:' + restarted_port
        state = ready()
        assert state['cafes'][0]['id'] == cafe['id']
        assert state['cafes'][0]['name'] == 'Linux 容器网吧'
        assert 'tokenHash' not in state['cafes'][0]
        print('PASS Linux container startup, webpage, authenticated API and volume persistence')
    finally:
        if container:
            print(command('logs', container))
            command('rm', '-f', container)
        command('volume', 'rm', volume)


if __name__ == '__main__':
    main()
