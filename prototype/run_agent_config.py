import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

import agent
from runtime import configure_console


class OutputLog:
    def __init__(self, console, file):
        self.console = console
        self.file = file

    def write(self, value):
        self.file.write(value)
        self.file.flush()
        if self.console:
            try:
                self.console.write(value)
                self.console.flush()
            except UnicodeError:
                encoding = getattr(self.console, 'encoding', None) or 'ascii'
                fallback = value.encode(encoding, 'backslashreplace').decode(encoding)
                try:
                    self.console.write(fallback)
                    self.console.flush()
                except (OSError, ValueError, UnicodeError):
                    self.console = None
            except (OSError, ValueError):
                self.console = None
        return len(value)

    def flush(self):
        self.file.flush()
        if self.console:
            try:
                self.console.flush()
            except (OSError, ValueError, UnicodeError):
                self.console = None


def agent_arguments(config, folder):
    for field in ('cafeId', 'server', 'agentToken'):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError('配置缺少 ' + field + '，请在网页添加网吧并下载 agent-config.json')
    parsed = urlparse(config['server'])
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('服务器地址必须以 http:// 或 https:// 开头')
    adapter = os.path.abspath(os.path.join(folder, config.get('adapter') or 'PcstoryAdapter.exe'))
    arguments = ['Agent.exe', '--cafe-id', config['cafeId'], '--name', config.get('name', '网吧'),
                 '--server', config['server'], '--agent-token', config['agentToken'], '--adapter', adapter,
                 '--inventory-interval', str(int(config.get('inventoryInterval', 30)))]
    if config.get('pcstoryFolder'):
        arguments.extend(['--pcstory-folder', os.path.abspath(os.path.join(folder, config['pcstoryFolder']))])
    return arguments


def lock_instance(file):
    if os.name == 'nt':
        import msvcrt
        file.seek(0)
        if not file.read(1):
            file.write(b'0')
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def main():
    configure_console()
    base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.path.join(base, 'agent-config.json'))
    args = parser.parse_args()
    path = os.path.abspath(args.config)
    folder = os.path.dirname(path)
    os.chdir(folder)
    with open('agent-instance.lock', 'a+b') as instance, open('agent.log', 'a', encoding='utf-8') as output:
        original_out, original_err = sys.stdout, sys.stderr
        sys.stdout = OutputLog(original_out, output)
        sys.stderr = OutputLog(original_err, output)
        try:
            try:
                lock_instance(instance)
            except OSError:
                raise ValueError('此目录的 Agent 已经运行，请勿重复启动')
            print('\n[%s] Agent 启动，日志保存在 agent.log' % time.strftime('%Y-%m-%d %H:%M:%S'))
            with open(path, encoding='utf-8-sig') as file:
                config = json.load(file)
            sys.argv = agent_arguments(config, folder)
            agent.main()
        except KeyboardInterrupt:
            print('Agent 已停止')
        except Exception as error:
            print('启动失败：', error)
            return 1
        finally:
            sys.stdout, sys.stderr = original_out, original_err
    return 0


if __name__ == '__main__':
    sys.exit(main())
