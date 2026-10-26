"""Exercise the interactive menu with a terminal and realistic pct list output."""
import errno
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'lxcrun'


class SelectionTests(unittest.TestCase):
    def run_selection(self, response, args=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock = root / 'pct'
            mock.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'list':
    print('%-10s %-10s %-12s %-20s' % ('VMID', 'Status', 'Lock', 'Name'))
    for row in [('100', 'running', '', 'web-server'),
                ('101', 'stopped', '', 'database'),
                ('102', 'running', 'backup', 'backup-server')]:
        print('%-10s %-10s %-12s %-20s' % row)
elif args[0] == 'config':
    print('hostname: database')
elif args[0] == 'status':
    print('status: running')
''')
            mock.chmod(0o755)
            logger = root / 'logger'
            logger.write_text('#!/bin/sh\nexit 0\n')
            logger.chmod(0o755)
            job = root / 'job.sh'
            job.write_text('#!/bin/sh\ntrue\n')
            args = ['--remote', 'true'] if args is None else args
            args = [str(job) if arg == 'LOCAL_SCRIPT' else arg for arg in args]
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       CALLS=str(root / 'calls'))
            master, slave = pty.openpty()
            process = None
            output = bytearray()
            try:
                process = subprocess.Popen(
                    ['bash', str(SCRIPT), *args],
                    stdin=slave, stdout=slave, stderr=slave, env=env)
                os.close(slave)
                slave = None
                deadline = time.monotonic() + 10
                answered = False
                while time.monotonic() < deadline:
                    if not answered and b'Enter to cancel' in output:
                        os.write(master, response)
                        answered = True
                    if not select.select([master], [], [], 0.1)[0]:
                        continue
                    try:
                        data = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not data:
                        break
                    output.extend(data)
                else:
                    self.fail('Interactive selection timed out')
                code = process.wait(timeout=2)
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
                if slave is not None:
                    os.close(slave)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            return code, output.decode(), calls

    def test_menu_shows_hostnames_with_blank_and_populated_lock_columns(self):
        code, output, calls = self.run_selection(b'\n')
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r'VMID\s+Hostname\s+State')
        self.assertRegex(output, r'1\)\s+100\s+web-server\s+\[running\]')
        self.assertRegex(output, r'2\)\s+101\s+database\s+\[stopped\]')
        self.assertRegex(output, r'3\)\s+102\s+backup-server\s+\[running\]')
        self.assertEqual(calls, [['list']])

    def test_numbered_selection_executes_the_displayed_container(self):
        code, output, calls = self.run_selection(b'2\n')
        self.assertEqual(code, 0, output)
        self.assertIn('[101 database] Beginning execution', output)
        self.assertEqual([call for call in calls if call[0] == 'exec'],
                         [['exec', '101', '--', 'true']])

    def test_local_mode_interactive_selection_preserves_script_arguments(self):
        code, output, calls = self.run_selection(b'2\n', ['--local', 'LOCAL_SCRIPT', '-y'])
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r'2\)\s+101\s+database\s+\[stopped\]')
        pushes = [call for call in calls if call[0] == 'push']
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][1], '101')
        self.assertIn(['exec', '101', '--', pushes[0][3], '-y'], calls)

    def test_shorthand_local_path_uses_interactive_selection(self):
        code, output, calls = self.run_selection(b'2\n', ['LOCAL_SCRIPT', '-y'])
        self.assertEqual(code, 0, output)
        pushes = [call for call in calls if call[0] == 'push']
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][1], '101')
        self.assertIn(['exec', '101', '--', pushes[0][3], '-y'], calls)
