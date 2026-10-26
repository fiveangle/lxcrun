"""Run with python3 -m unittest discover -s tests -v; no Proxmox host required."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'lxcrun'
AUTO = 'lxcrun-20261109-123456'
OTHER_AUTO = 'lxcrun-20261109-123457'
LEGACY_AUTO = AUTO + '-123456789-1234'


class CleanupTests(unittest.TestCase):
    def run_cleanup(self, args, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock = root / 'pct'
            mock.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *args]) + '\\n')
s = json.loads(os.environ['SCENARIO'])
if Path(sys.argv[0]).name == 'pvesh':
    cid = args[1].split('/')[4]
    if cid in s.get('list_fail', []): sys.exit(1)
    print(s.get('raw', json.dumps(s.get('snapshots', []))))
elif args[0] == 'list':
    if s.get('inventory_fail'): sys.exit(1)
    print('VMID Status Lock Name')
    for cid in s.get('ids', ['100']): print(cid, ('running' if cid in s.get('running', []) else 'stopped'), '- test')
elif args[0] == 'config':
    if args[1] in s.get('config_fail', []): sys.exit(1)
    print('hostname: test')
    if args[1] in s.get('locked', []) or Path(os.environ['RACE']).exists():
        print('lock: backup')
elif args[0] == 'delsnapshot':
    if s.get('migration_lock'):
        print('Failed to obtain guest migration lock - replication running?', file=sys.stderr)
        sys.exit(1)
    if s.get('race'):
        Path(os.environ['RACE']).touch()
        sys.exit(1)
    if args[2] in s.get('delete_fail', []): sys.exit(1)
else:
    sys.exit('unexpected command: ' + str(args))
''')
            mock.chmod(0o755)
            logger = root / 'logger'
            logger.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\ns = json.loads(os.environ['SCENARIO'])\nif s.get('logger_fail'): sys.exit(1)\nwith open(Path(__file__).with_name('syslog'), 'a') as log:\n    log.write(json.dumps(sys.argv[1:])+'\\n')\n")
            logger.chmod(0o755)

            (root / 'pvesh').symlink_to(mock)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       SCENARIO=json.dumps(scenario), CALLS=str(root/'calls'), RACE=str(root/'race'))
            result = subprocess.run(['bash', str(SCRIPT), *([] if any(a.startswith('--delete-snapshots') for a in args) else ['--delete-snapshots']), *args],
                                    env=env, text=True, capture_output=True)
            calls = [json.loads(line) for line in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
            return result, calls

    def test_only_exact_names_and_deduplicate_containers(self):
        names = [AUTO, 'manual', 'lxcrun-before-upgrade', AUTO+'-extra', AUTO[:-1]+'x',
                 AUTO+'-123456789', AUTO+'-1234', AUTO+'-12345678-1234', 'current']
        r, calls = self.run_cleanup(['100', '100'], {'snapshots': [
            {'name': name, 'description': OTHER_AUTO} for name in names]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[1] == 'delsnapshot'], [['pct', 'delsnapshot', '100', AUTO]])
        self.assertIn('1 deleted', r.stdout)
        self.assertIn('1 total', r.stdout)

    def test_old_suffix_names_are_ignored(self):
        r, calls = self.run_cleanup(['100'], {'snapshots': [{'name': AUTO}, {'name': LEGACY_AUTO}]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c[3] for c in calls if c[1] == 'delsnapshot'], [AUTO])
        self.assertIn('1 deleted', r.stdout)

    def test_named_cleanup_preserves_other_snapshots(self):
        for target in [AUTO, 'before-upgrade', 'release.1']:
            with self.subTest(target=target):
                snapshots = [{'name': n} for n in [target, target+'-extra', OTHER_AUTO, 'releaseX1', 'current']]
                r, calls = self.run_cleanup(['--delete-snapshots='+target, '100', '101'], {'snapshots': snapshots})
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([c for c in calls if c[1] == 'delsnapshot'],
                                 [['pct', 'delsnapshot', cid, target] for cid in ['100', '101']])

    def test_named_cleanup_all_and_locks(self):
        r, calls = self.run_cleanup(['--delete-snapshots='+AUTO, '--all'], {
            'ids': ['100', '101'], 'locked': ['100'],
            'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn('1 locked/skipped', r.stdout)
        self.assertIn('[100 test]', r.stderr)
        self.assertIn('Containers with warnings, failures, or skips:', r.stderr)
        self.assertEqual([c for c in calls if c[1] == 'delsnapshot'], [['pct', 'delsnapshot', '101', AUTO]])

    def test_named_cleanup_no_match(self):
        r, calls = self.run_cleanup(['--delete-snapshots=missing', '100'], {'snapshots': [{'name': AUTO}]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('No matching snapshots', r.stdout)
        self.assertFalse(any(c[1] == 'delsnapshot' for c in calls))

    def test_invalid_cleanup_names(self):
        for name in ['', 'current', '*', '-force', 'foo\nbar']:
            r, calls = self.run_cleanup(['--delete-snapshots='+name, '100'], {})
            self.assertEqual(r.returncode, 2)
            self.assertFalse(calls)

    def test_locked_container_skipped_and_next_processed(self):
        r, calls = self.run_cleanup(['--all'], {'ids': ['100', '101'], 'locked': ['100'], 'snapshots': [{'name': AUTO}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn('1 locked/skipped', r.stdout)
        self.assertIn('[100 test]', r.stderr)
        self.assertIn('Containers with warnings, failures, or skips:', r.stderr)
        self.assertEqual([c for c in calls if c[1] == 'delsnapshot'], [['pct', 'delsnapshot', '101', AUTO]])

    def test_lock_appearing_during_delete_stops_container(self):
        r, calls = self.run_cleanup(['100'], {'race': True, 'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn('1 locked/skipped', r.stdout)
        self.assertIn('[100 test]', r.stderr)
        self.assertIn('Containers with warnings, failures, or skips:', r.stderr)
        self.assertEqual(len([c for c in calls if c[1] == 'delsnapshot']), 1)

    def test_delete_failure_continues(self):
        r, calls = self.run_cleanup(['100'], {'delete_fail': [AUTO], 'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn('1 deleted; 1 deletion failures', r.stdout)
        self.assertEqual(len([c for c in calls if c[1] == 'delsnapshot']), 2)

    def test_migration_lock_skipped(self):
        r, calls = self.run_cleanup(['100'], {'migration_lock': True, 'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn('1 locked/skipped', r.stdout)
        self.assertIn('[100 test]', r.stderr)
        self.assertIn('Containers with warnings, failures, or skips:', r.stderr)
        self.assertEqual(len([c for c in calls if c[1] == 'delsnapshot']), 1)

    def test_read_errors_never_delete(self):
        for scenario in [{'raw': 'not json'}, {'raw': '{}'}, {'raw': json.dumps([{'name': AUTO}, {}])},
                         {'config_fail': ['100']}, {'list_fail': ['100']}]:
            with self.subTest(scenario=scenario):
                r, calls = self.run_cleanup(['100'], scenario)
                self.assertEqual(r.returncode, 1)
                self.assertIn('1 failed', r.stdout)
                self.assertFalse(any(c[1] == 'delsnapshot' for c in calls))

    def test_empty_inventory_and_no_matches(self):
        for args, scenario in [(['--all'], {'ids': []}), (['100'], {'snapshots': [{'name': 'manual'}]})]:
            r, calls = self.run_cleanup(args, scenario)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('0 deleted', r.stdout)
            self.assertFalse(any(c[1] == 'delsnapshot' for c in calls))

    def test_invalid_arguments(self):
        for args in [['--snapshot', '100'], ['100', '--', 'arg'], ['script.sh']]:
            r, calls = self.run_cleanup(args, {})
            self.assertEqual(r.returncode, 2)
            self.assertFalse(calls)

    def test_state_selection_cleanup(self):
        for selector, cid in [('--running', '100'), ('--stopped', '101')]:
            r, calls = self.run_cleanup([selector], {'ids': ['100', '101'], 'running': ['100'], 'snapshots': [{'name': AUTO}]})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([c for c in calls if c[1] == 'delsnapshot'], [['pct', 'delsnapshot', cid, AUTO]])

    def test_cleanup_exclusions_leave_containers_untouched(self):
        scenario = {'ids': ['100', '101', '102', '103'], 'running': ['100', '101'],
                    'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]}
        for selector, expected in [('--all', ['100', '102']),
                                   ('--running', ['100']), ('--stopped', ['102'])]:
            with self.subTest(selector=selector):
                r, calls = self.run_cleanup(
                    ['--delete-snapshots='+AUTO, '-101', selector, '-103'], scenario)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([c for c in calls if c[1] == 'delsnapshot'],
                                 [['pct', 'delsnapshot', cid, AUTO] for cid in expected])
                self.assertFalse(any(c[0] == 'pct' and len(c) > 2 and c[2] in ['101', '103'] for c in calls))
                self.assertFalse(any(c[0] == 'pvesh' and c[2].split('/')[4] in ['101', '103'] for c in calls))
                self.assertIn(f'{len(expected)} total', r.stdout)

    def test_cleanup_with_all_containers_excluded_is_successful_noop(self):
        r, calls = self.run_cleanup(['--all', '-100'], {'snapshots': [{'name': AUTO}]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, [['pct', 'list']])
        self.assertIn('0 deleted', r.stdout)
        self.assertIn('0 total', r.stdout)

    def test_cleanup_exclusions_require_group_selection(self):
        for args in [['-100'], ['100', '-101']]:
            r, calls = self.run_cleanup(args, {})
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertFalse(calls)

    def test_cleanup_expands_group_without_starting_added_containers(self):
        scenario = {'ids': ['100', '101', '103'], 'running': ['100', '101'],
                    'snapshots': [{'name': AUTO}, {'name': OTHER_AUTO}]}
        r, calls = self.run_cleanup(
            ['--delete-snapshots='+AUTO, '--running', '103', '100', '103', '-101'], scenario)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[1] == 'delsnapshot'],
                         [['pct', 'delsnapshot', cid, AUTO] for cid in ['100', '103']])
        self.assertFalse(any(c[0] == 'pct' and len(c) > 2 and c[2] == '101' for c in calls))
        self.assertFalse(any(c[1] in ['start', 'shutdown'] for c in calls))
        self.assertIn('2 total', r.stdout)

    def test_cleanup_exclusion_wins_over_added_vmid(self):
        r, calls = self.run_cleanup(
            ['--running', '-00103', '00103', '103'],
            {'ids': ['100', '103'], 'running': ['100'], 'snapshots': [{'name': AUTO}]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[1] == 'delsnapshot'],
                         [['pct', 'delsnapshot', '100', AUTO]])
        self.assertFalse(any(c[0] == 'pct' and len(c) > 2 and c[2] == '103' for c in calls))

    def test_inventory_failure(self):
        r, calls = self.run_cleanup(['--all'], {'inventory_fail': True})
        self.assertEqual(r.returncode, 2)
        self.assertFalse(any(c[1] == 'delsnapshot' for c in calls))


if __name__ == '__main__':
    unittest.main()
