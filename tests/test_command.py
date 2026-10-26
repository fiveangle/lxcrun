"""Command execution integration tests using a fake pct."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'lxcrun'


class CommandTests(unittest.TestCase):
    def run_lxcrun(self, args, scenario=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock = root / 'pct'
            mock.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['LOG'], 'a') as log: log.write(json.dumps(args)+'\\n')
s = json.loads(os.environ['SCENARIO'])
started = Path(os.environ['STARTED'])
if args[0] == 'list':
    print('VMID Status Lock Name')
    for cid in s.get('ids', ['100', '101']): print(cid, ('stopped' if cid in s.get('stopped', []) else 'running'), '- test')
elif args[0] == 'status':
    if args[1] in s.get('missing', []): sys.exit(1)
    print('status: stopped' if args[1] in s.get('stopped', []) and not started.exists() else 'status: running')
elif args[0] == 'config':
    if args[1] in s.get('missing', []): sys.exit(1)
    print('hostname: test')
    if args[1] in s.get('templates', []): print('template: 1')
elif args[0] == 'exec' and args[3:6] == ['rm', '-f', '--'] and s.get('cleanup_fail'): sys.exit(1)
elif args[0] == 'exec' and args[3].startswith('/tmp/lxcrun-') and args[1] in s.get('script_fail', []): sys.exit(7)
elif args[0] == 'start': started.touch()
elif args[0] == 'snapshot' and args[1] in s.get('snapshot_fail', []): sys.exit(1)
elif args[0] == 'exec' and args[1] in s.get('exec_fail', []): sys.exit(7)
''')
            mock.chmod(0o755)
            logger = root / 'logger'
            logger.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\ns = json.loads(os.environ['SCENARIO'])\nif s.get('logger_fail'): sys.exit(1)\nwith open(Path(__file__).with_name('syslog'), 'a') as log:\n    log.write(json.dumps(sys.argv[1:])+'\\n')\n")
            logger.chmod(0o755)

            job = root/'job.sh'
            job.write_text('#!/bin/sh\ntrue\n')
            args = [str(job) if a == 'LOCAL_SCRIPT' else './job.sh' if a == 'RELATIVE_SCRIPT' else a for a in args]
            env = dict(os.environ, PATH=tmp+':'+os.environ['PATH'], LOG=str(root/'log'),
                       STARTED=str(root/'started'), SCENARIO=json.dumps(scenario or {}))
            r = subprocess.run(['bash', str(SCRIPT), *args], env=env, cwd=root, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True)
            calls = [json.loads(line) for line in (root/'log').read_text().splitlines()] if (root/'log').exists() else []
            r.syslog = (root/'syslog').read_text() if (root/'syslog').exists() else ''
            return r, calls

    def test_named_failure_summary_and_syslog(self):
        r, calls = self.run_lxcrun(['100', '101', '--remote', 'false'], {'exec_fail': ['100']})
        self.assertEqual(r.returncode, 1)
        self.assertIn('[100 test] Beginning execution', r.stdout)
        summary = r.stderr.split('Containers with warnings, failures, or skips:')[1]
        self.assertIn('[100 test] FAILED (exit 7)', summary)
        self.assertNotIn('[101 test]', summary)
        self.assertIn('user.err', r.syslog)
        self.assertIn('[100 test] FAILED (exit 7)', r.syslog)
        self.assertIn('[101 test] OK', r.syslog)
        self.assertIn('run=', r.syslog)

    def test_warning_summary_and_logger_failure(self):
        r, calls = self.run_lxcrun(['100', '--local', 'LOCAL_SCRIPT'], {'cleanup_fail': True})
        self.assertEqual(r.returncode, 0)
        self.assertIn('Containers with warnings, failures, or skips:', r.stderr)
        self.assertIn('[100 test] WARNING: could not remove', r.stderr)
        self.assertIn('user.warning', r.syslog)
        r, calls = self.run_lxcrun(['--all', '--remote', 'true'], {'logger_fail': True})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.count('syslog logging unavailable'), 1)
        self.assertIn('[101 test] OK', r.stdout)

    def test_all_and_literal_arguments(self):
        command = ['printf', '%s', 'two words', '', '*', '$(touch unwanted)', '--help', '--running', '--stopped', '--all', '--snapshot', '--remote', '--local', '--', '100', '-100', '-101']
        r, calls = self.run_lxcrun(['--all', '--remote', *command])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[0] == 'exec'], [['exec', cid, '--', *command] for cid in ['100', '101']])
        self.assertFalse(any(c[0] == 'push' for c in calls))

    def test_snapshot_and_restore_after_failed_command(self):
        r, calls = self.run_lxcrun(['--snapshot=before', '--restore-state', '100', '101', '--remote', 'false'],
                                 {'stopped': ['100'], 'exec_fail': ['100']})
        self.assertEqual(r.returncode, 1)
        self.assertLess(calls.index(['snapshot', '100', 'before']), calls.index(['start', '100']))
        self.assertLess(calls.index(['exec', '100', '--', 'false']), calls.index(['shutdown', '100']))
        self.assertIn(['exec', '101', '--', 'false'], calls)
        self.assertNotIn(['shutdown', '101'], calls)

    def test_skips_and_snapshot_failure(self):
        for options, scenario in [(['--no-start'], {'stopped': ['100']}),
                                  ([], {'templates': ['100']}),
                                  (['--snapshot'], {'snapshot_fail': ['100']})]:
            r, calls = self.run_lxcrun([*options, '100', '101', '--remote', 'true'], scenario)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn(['exec', '100', '--', 'true'], calls)
            self.assertIn(['exec', '101', '--', 'true'], calls)

    def test_invalid_command_options(self):
        for args in [['--remote'], ['100', '--remote'], ['--all', '--remote', ''],
                     ['--keep', '--remote', 'true'],
                     ['--remote-dir', '/tmp', '--remote', 'true'],
                     ['--delete-snapshots', '--remote', 'true'],
                     ['100', 'dpkg', '--configure', 'locales']]:
            with self.subTest(args=args):
                r, calls = self.run_lxcrun(args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(calls)

    def test_state_selection(self):
        for selector, cid in [('--running', '100'), ('--stopped', '101')]:
            r, calls = self.run_lxcrun([selector, '--restore-state', '--remote', 'true'], {'stopped': ['101']})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([c for c in calls if c[0] == 'exec'], [['exec', cid, '--', 'true']])
            self.assertEqual(['start', '101'] in calls, selector == '--stopped')
            self.assertEqual(['shutdown', '101'] in calls, selector == '--stopped')

    def test_state_selection_scripts_and_no_start(self):
        r, calls = self.run_lxcrun(['--running', '--local', 'LOCAL_SCRIPT'], {'stopped': ['101']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'push'], ['100'])
        r, calls = self.run_lxcrun(['--stopped', '--no-start', '--remote', 'true'], {'stopped': ['101']})
        self.assertEqual(r.returncode, 1)
        self.assertFalse(any(c[0] in ['start', 'exec'] for c in calls))

    def test_state_selection_conflicts(self):
        for options in [['--running', '--stopped'], ['--stopped', '--running'],
                        ['--all', '--running'], ['--stopped', '--all']]:
            r, calls = self.run_lxcrun([*options, '--remote', 'true'])
            self.assertEqual(r.returncode, 2)
            self.assertFalse(calls)

    def test_empty_state_selection(self):
        for selector, scenario in [('--stopped', {}), ('--running', {'stopped': ['100', '101']})]:
            r, calls = self.run_lxcrun([selector, '--remote', 'true'], scenario)
            self.assertEqual(r.returncode, 0)
            self.assertIn('No matching LXC containers', r.stdout)
            self.assertEqual(calls, [['list']])

    def test_group_expansion_includes_opposite_state_and_deduplicates(self):
        scenario = {'ids': ['100', '101', '103', '104'], 'stopped': ['103', '104']}
        for options, expected in [(['--running', '103', '100', '103', '-101'], ['100', '103']),
                                  (['--stopped', '100', '103', '-104'], ['103', '100']),
                                  (['--all', '100', '103', '-101', '-104'], ['100', '103'])]:
            with self.subTest(options=options):
                r, calls = self.run_lxcrun([*options, '--remote', 'true'], scenario)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([c[1] for c in calls if c[0] == 'exec'], expected)
                self.assertEqual(sum(c == ['start', '103'] for c in calls), 1)
                self.assertIn('Completed successfully on 2 container(s)', r.stdout)

    def test_explicit_stopped_addition_with_local_shorthand(self):
        r, calls = self.run_lxcrun(
            ['--running', '103', '--snapshot=before', '--restore-state', 'RELATIVE_SCRIPT', '-y'],
            {'ids': ['100', '103'], 'stopped': ['103']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'push'], ['100', '103'])
        self.assertLess(calls.index(['snapshot', '103', 'before']), calls.index(['start', '103']))
        remote = next(c[3] for c in calls if c[0] == 'push' and c[1] == '103')
        self.assertIn(['exec', '103', '--', remote, '-y'], calls)
        self.assertIn(['shutdown', '103'], calls)
        self.assertNotIn(['shutdown', '100'], calls)

    def test_added_stopped_container_respects_no_start(self):
        r, calls = self.run_lxcrun(
            ['--running', '103', '--no-start', '--snapshot=before', '--remote', 'true'],
            {'ids': ['100', '103'], 'stopped': ['103']})
        self.assertEqual(r.returncode, 1)
        self.assertIn(['exec', '100', '--', 'true'], calls)
        self.assertFalse(any(c[0] in ['start', 'snapshot', 'exec'] and c[1] == '103' for c in calls))
        self.assertIn('SKIP: container is not running', r.stderr)

    def test_exclusions_win_over_explicit_additions_in_any_order(self):
        for options in [['--running', '103', '-103', '-101'],
                        ['-00103', '--running', '00103', '103', '-101']]:
            r, calls = self.run_lxcrun([*options, '--remote', 'true'], {'stopped': ['103']})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([c for c in calls if c[0] == 'exec'], [['exec', '100', '--', 'true']])
            self.assertFalse(any(len(c) > 1 and c[1] in ['101', '103', '00103'] for c in calls))

    def test_additions_survive_empty_group_and_do_not_require_inventory_membership(self):
        r, calls = self.run_lxcrun(
            ['--running', '103', '--remote', 'true'], {'stopped': ['100', '101', '103']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(['start', '103'], calls)
        self.assertEqual([c for c in calls if c[0] == 'exec'], [['exec', '103', '--', 'true']])

    def test_nonexistent_added_container_is_reported_as_failure(self):
        r, calls = self.run_lxcrun(['--running', '999', '--remote', 'true'], {'missing': ['999']})
        self.assertEqual(r.returncode, 1)
        self.assertIn('[999 unknown] ERROR: container does not exist', r.stderr)
        self.assertIn(['exec', '100', '--', 'true'], calls)
        self.assertIn(['exec', '101', '--', 'true'], calls)
        self.assertNotIn(['exec', '999', '--', 'true'], calls)

    def test_padded_ids_are_deduplicated_against_group(self):
        r, calls = self.run_lxcrun(['--all', '00100', '100', '--remote', 'true'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[0] == 'exec'],
                         [['exec', '100', '--', 'true'], ['exec', '101', '--', 'true']])

    def test_exclusions_can_empty_combined_group_and_additions(self):
        r, calls = self.run_lxcrun(['--all', '103', '-100', '-101', '-103', '--remote', 'true'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, [['list']])
        self.assertIn('No matching LXC containers', r.stdout)

    def test_group_exclusions_prevent_all_container_access(self):
        scenario = {'ids': ['100', '101', '102', '103'], 'stopped': ['103']}
        for selector, expected in [('--all', ['100', '103']),
                                   ('--running', ['100']), ('--stopped', ['103'])]:
            with self.subTest(selector=selector):
                r, calls = self.run_lxcrun(
                    [selector, '-101', '-102', '--snapshot=before', '--restore-state',
                     '--remote', 'true'], scenario)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([c[1] for c in calls if c[0] == 'exec'], expected)
                self.assertFalse(any(len(c) > 1 and c[1] in ['101', '102'] for c in calls))
                self.assertIn(f'Completed successfully on {len(expected)} container(s)', r.stdout)
                self.assertNotIn('[101 ', r.syslog)
                self.assertNotIn('[102 ', r.syslog)

    def test_stopped_exclusion_is_not_started(self):
        r, calls = self.run_lxcrun(
            ['--stopped', '-101', '--restore-state', '--remote', 'true'],
            {'ids': ['100', '101'], 'stopped': ['100', '101']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(['start', '100'], calls)
        self.assertIn(['shutdown', '100'], calls)
        self.assertFalse(any(len(c) > 1 and c[1] == '101' for c in calls))

    def test_local_exclusions_before_mode_switch(self):
        for args in [['--all', '-101', '--local', 'LOCAL_SCRIPT'],
                     ['-101', '--all', '--local', 'LOCAL_SCRIPT']]:
            with self.subTest(args=args):
                r, calls = self.run_lxcrun(args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([c[1] for c in calls if c[0] == 'push'], ['100'])
                self.assertFalse(any(len(c) > 1 and c[1] == '101' for c in calls))

    def test_script_negative_arguments_are_literal(self):
        r, calls = self.run_lxcrun(['--all', '-101', '--local', 'LOCAL_SCRIPT', '-100', '-101'])
        self.assertEqual(r.returncode, 0, r.stderr)
        remote = next(c[3] for c in calls if c[0] == 'push')
        self.assertIn(['exec', '100', '--', remote, '-100', '-101'], calls)
        self.assertFalse(any(len(c) > 1 and c[1] == '101' for c in calls))

    def test_exclusions_are_numeric_and_allow_duplicates_and_unmatched_ids(self):
        r, calls = self.run_lxcrun(
            ['-00101', '--running', '-101', '-999', '-102', '--remote', 'true'],
            {'ids': ['100', '101', '102'], 'stopped': ['102']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c[0] == 'exec'], [['exec', '100', '--', 'true']])
        self.assertFalse(any(len(c) > 1 and c[1] in ['101', '102', '999'] for c in calls))

    def test_excluding_entire_group_is_successful_noop(self):
        for selector in ['--all', '--running', '--stopped']:
            with self.subTest(selector=selector):
                r, calls = self.run_lxcrun(
                    [selector, '-100', '-101', '--remote', 'true'])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('No matching LXC containers', r.stdout)
                self.assertEqual(calls, [['list']])

    def test_exclusions_require_group_selection(self):
        for args in [['-101', '--remote', 'true'],
                     ['100', '-101', '--remote', 'true'],
                     ['-101', '--local', 'LOCAL_SCRIPT']]:
            with self.subTest(args=args):
                r, calls = self.run_lxcrun(args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(calls)

    def test_malformed_exclusions_fail_before_access(self):
        for exclusion in ['-101x', '-101,102', '-10.1', '--101', '-']:
            for args in [['--running', exclusion, '--remote', 'true'],
                         ['--running', exclusion, '--local', 'LOCAL_SCRIPT']]:
                with self.subTest(args=args):
                    r, calls = self.run_lxcrun(args)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertFalse(calls)

    def test_remote_path_is_never_copied_or_validated_on_host(self):
        for path in ['LOCAL_SCRIPT', '/container-only/maintenance.sh']:
            r, calls = self.run_lxcrun(['--running', '-101', '--remote', path, '-y'])
            self.assertEqual(r.returncode, 0, r.stderr)
            executions = [c for c in calls if c[0] == 'exec']
            self.assertEqual(len(executions), 1)
            self.assertEqual(executions[0][:3], ['exec', '100', '--'])
            self.assertEqual(executions[0][-1], '-y')
            if path == 'LOCAL_SCRIPT':
                self.assertEqual(Path(executions[0][3]).name, 'job.sh')
            else:
                self.assertEqual(executions[0][3], path)
            self.assertFalse(any(c[0] == 'push' for c in calls))

    def test_local_arguments_are_literal_and_do_not_change_selection(self):
        arguments = ['-y', '--help', '--running', '--remote', '--local', '--', '-100', '101', '', 'two words', '*']
        r, calls = self.run_lxcrun(['100', '--local', 'LOCAL_SCRIPT', *arguments])
        self.assertEqual(r.returncode, 0, r.stderr)
        pushes = [c for c in calls if c[0] == 'push']
        self.assertEqual(len(pushes), 1)
        self.assertIn(['exec', '100', '--', pushes[0][3], *arguments], calls)
        self.assertFalse(any(len(c) > 1 and c[1] == '101' for c in calls))

    def test_local_shorthand_matches_explicit_local_mode(self):
        scenario = {'ids': ['100', '103', '111']}
        for payload in [['LOCAL_SCRIPT'], ['RELATIVE_SCRIPT'],
                        ['--local', 'LOCAL_SCRIPT'], ['--local', 'job.sh']]:
            with self.subTest(payload=payload):
                r, calls = self.run_lxcrun(
                    ['--running', '-103', '-111', '--snapshot=before', *payload, '-y'],
                    scenario)
                self.assertEqual(r.returncode, 0, r.stderr)
                pushes = [c for c in calls if c[0] == 'push']
                self.assertEqual(len(pushes), 1)
                self.assertEqual(pushes[0][1], '100')
                self.assertEqual(Path(pushes[0][2]).name, 'job.sh')
                self.assertIn(['exec', '100', '--', pushes[0][3], '-y'], calls)
                self.assertIn(['snapshot', '100', 'before'], calls)
                self.assertFalse(any(len(c) > 1 and c[1] in ['103', '111'] for c in calls))

    def test_shorthand_path_ends_lxcrun_argument_parsing(self):
        arguments = ['-101', '--running', '--remote', '--local', '--help', '--', 'two words']
        r, calls = self.run_lxcrun(['--all', 'RELATIVE_SCRIPT', *arguments])
        self.assertEqual(r.returncode, 0, r.stderr)
        pushes = [c for c in calls if c[0] == 'push']
        self.assertEqual([c[1] for c in pushes], ['100', '101'])
        for _, cid, source, remote in pushes:
            self.assertEqual(source, './job.sh')
            self.assertIn(['exec', cid, '--', remote, *arguments], calls)

    def test_shorthand_supports_local_options_and_state_restoration(self):
        r, calls = self.run_lxcrun(
            ['--stopped', '-101', '--restore-state', '--keep', '--remote-dir',
             '/root/scripts', 'RELATIVE_SCRIPT', '-y'], {'stopped': ['100', '101']})
        self.assertEqual(r.returncode, 0, r.stderr)
        remote = next(c[3] for c in calls if c[0] == 'push')
        self.assertTrue(remote.startswith('/root/scripts/'))
        self.assertIn(['start', '100'], calls)
        self.assertIn(['shutdown', '100'], calls)
        self.assertNotIn(['exec', '100', '--', 'rm', '-f', '--', remote], calls)

    def test_shorthand_requires_readable_host_file_and_cannot_be_cleanup(self):
        for args in [['--all', './missing.sh', '-y'],
                     ['--all', '/container-only/script.sh', '-y'],
                     ['--delete-snapshots', 'LOCAL_SCRIPT']]:
            with self.subTest(args=args):
                r, calls = self.run_lxcrun(args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(calls)

    def test_local_options_snapshots_and_state_restoration(self):
        r, calls = self.run_lxcrun(
            ['--stopped', '-101', '--snapshot=before', '--restore-state', '--keep',
             '--remote-dir', '/root/scripts', '--local', 'LOCAL_SCRIPT', '-y'],
            {'stopped': ['100', '101']})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(calls.index(['snapshot', '100', 'before']), calls.index(['start', '100']))
        remote = next(c[3] for c in calls if c[0] == 'push')
        self.assertTrue(remote.startswith('/root/scripts/'))
        self.assertIn(['exec', '100', '--', remote, '-y'], calls)
        self.assertIn(['shutdown', '100'], calls)
        self.assertNotIn(['exec', '100', '--', 'rm', '-f', '--', remote], calls)
        self.assertFalse(any(len(c) > 1 and c[1] == '101' for c in calls))

    def test_local_failure_restores_state_and_continues(self):
        r, calls = self.run_lxcrun(
            ['--restore-state', '100', '101', '--local', 'LOCAL_SCRIPT', '-y'],
            {'stopped': ['100'], 'script_fail': ['100']})
        self.assertEqual(r.returncode, 1)
        remote = next(c[3] for c in calls if c[0] == 'push' and c[1] == '100')
        self.assertIn(['exec', '100', '--', remote, '-y'], calls)
        self.assertIn(['exec', '100', '--', 'rm', '-f', '--', remote], calls)
        self.assertIn(['shutdown', '100'], calls)
        self.assertTrue(any(c[0] == 'push' and c[1] == '101' for c in calls))

    def test_invalid_local_options_and_missing_modes(self):
        for args in [['--local'], ['100', '--local'], ['--all', '--local', ''],
                     ['--all', '--local', '/missing/script.sh'],
                     ['--remote-dir', 'relative', '100', '--local', 'LOCAL_SCRIPT'],
                     ['--delete-snapshots', '--local', 'LOCAL_SCRIPT'],
                     ['--local', 'dpkg', '-y'],
                     ['--all', 'job.sh', '-y'],
                     ['--all', '--command', 'true'], ['--all', '--script', 'LOCAL_SCRIPT'],
                     ['--all', '--', 'true'], ['--running']]:
            with self.subTest(args=args):
                r, calls = self.run_lxcrun(args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn('--help', r.stderr)
                self.assertFalse(calls)

    def test_help_and_compact_overview(self):
        for flag in ['-h', '--help']:
            r, calls = self.run_lxcrun([flag])
            self.assertEqual(r.returncode, 0)
            self.assertIn('--remote', r.stdout)
            self.assertIn('--local', r.stdout)
            self.assertIn('--running -100 -101 --remote', r.stdout)
            self.assertIn('--running -100 -101 --local', r.stdout)
            self.assertIn('--running -100 -101 ./maintenance.sh -y', r.stdout)
            self.assertNotIn('--command', r.stdout)
            self.assertNotIn('--script', r.stdout)
            self.assertFalse(calls)
        r, calls = self.run_lxcrun([])
        self.assertEqual(r.returncode, 2)
        self.assertIn('--local', r.stderr)
        self.assertIn('--remote', r.stderr)
        self.assertLessEqual(len(r.stderr.splitlines()), 24)
        self.assertFalse(calls)

    def test_shell_is_explicit(self):
        command = ['sh', '-c', 'echo one && echo two']
        r, calls = self.run_lxcrun(['100', '--remote', *command])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(['exec', '100', '--', *command], calls)

    def test_script_mode_still_copies_and_cleans_up(self):
        r, calls = self.run_lxcrun(['--all', '--local', 'LOCAL_SCRIPT', 'two words'])
        self.assertEqual(r.returncode, 0, r.stderr)
        pushes = [c for c in calls if c[0] == 'push']
        self.assertEqual(len(pushes), 2)
        for _, cid, local, remote in pushes:
            self.assertIn(['exec', cid, '--', remote, 'two words'], calls)
            self.assertIn(['exec', cid, '--', 'rm', '-f', '--', remote], calls)
