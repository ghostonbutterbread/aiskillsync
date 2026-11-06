from pathlib import Path
import os
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiskillsync.agents import build_agent_sync_plan, apply_agent_sync_plan
from aiskillsync.config import config_from_mapping


class AgentRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'src' / 'agents'
        self.source.mkdir(parents=True)
        self.agent = self.source / 'reviewer.md'
        self.agent.write_text('---\nname: reviewer\ndescription: Review\n---\nOriginal.\n')
        self.dest = self.root / 'dest'
        self.backup = self.root / 'backup'
        self.raw = {'bridges': [{'name': 'src', 'path': str(self.source.parent), 'agents_path': 'agents'}],
                    'ai_agent_paths': {'test': str(self.dest)},
                    'sync': {'pull_before_sync': False, 'clone_if_missing': False}}

    def plan(self, adopt=False):
        return build_agent_sync_plan(config_from_mapping(self.raw, self.root / 'config'), ('src',), ('test',),
                                     dry_run=False, adopt=adopt, backup_root=self.backup)

    def test_source_crlf_only_change_invalidates_plan(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        plan = self.plan()
        self.agent.write_bytes(self.agent.read_bytes().replace(b'\n', b'\r\n'))
        with self.assertRaisesRegex(ValueError, 'source changed'):
            apply_agent_sync_plan(plan)
        self.assertFalse((self.dest / 'reviewer.md').exists())

    def test_edit_after_ownership_check_cannot_be_automatically_refreshed(self):
        import aiskillsync.agents as agents
        self.raw['agent_formats'] = {'test': 'omp'}
        apply_agent_sync_plan(self.plan())
        target = self.dest / 'reviewer.md'
        self.agent.write_text(self.agent.read_text() + 'updated\n')
        original = agents._owned_native
        edited = target.read_bytes().replace(b'Original.', b'LOCAL!!!.')
        self.assertEqual(len(edited), target.stat().st_size)
        def edit_after_check(*args):
            result = original(*args)
            st = target.stat()
            target.write_bytes(edited)
            os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
            return result
        with patch('aiskillsync.agents._owned_native', side_effect=edit_after_check):
            plan = self.plan()
        if not plan.has_blockers:
            with self.assertRaises(ValueError):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), edited)

    def test_stable_read_rejects_edit_during_read_with_restored_mtime(self):
        from aiskillsync.agents import _stable_file_bytes
        target = self.root / 'local'
        target.write_bytes(b'original')
        original_stat = target.stat()
        fstat = os.fstat
        calls = 0
        def edit_at_second_stat(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write_bytes(b'modified')
                os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            return fstat(fd)
        with patch('aiskillsync.agents.os.fstat', side_effect=edit_at_second_stat):
            with self.assertRaisesRegex(ValueError, 'changed while reading'):
                _stable_file_bytes(target)

    def test_destination_root_swap_at_publication_cannot_write_source(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        (self.dest / 'reviewer.md').write_text('local')
        plan = self.plan(adopt=True)
        original = self.agent.read_bytes()
        link = os.link
        def swap(src, dst, *args, **kwargs):
            self.dest.rename(self.root / 'dest-moved')
            self.dest.symlink_to(protected, target_is_directory=True)
            return link(src, dst, *args, **kwargs)
        with patch('aiskillsync.agents.os.link', side_effect=swap):
            try:
                apply_agent_sync_plan(plan)
            except (ValueError, OSError):
                pass
        self.assertEqual(self.agent.read_bytes(), original)
        self.assertFalse((protected / 'reviewer.md').exists())

    def test_other_process_root_swap_at_displacement_preserves_source_and_recovery(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('local')
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        protected_target = protected / 'reviewer.md'
        protected_target.write_text('protected')
        plan = self.plan(adopt=True)
        import aiskillsync.agents as agents
        rename = agents._rename_noreplace
        def race(src, dst, *args, **kwargs):
            subprocess.run([sys.executable, '-c',
                            'import os,sys; os.rename(sys.argv[1],sys.argv[2]); os.symlink(sys.argv[3],sys.argv[1])',
                            str(self.dest), str(self.root / 'dest-moved'), str(protected)], check=True)
            return rename(src, dst, *args, **kwargs)
        with patch('aiskillsync.agents._rename_noreplace', side_effect=race):
            try:
                apply_agent_sync_plan(plan)
            except (ValueError, OSError):
                pass
        self.assertTrue(protected_target.exists(), 'protected source entry was displaced')
        self.assertEqual(protected_target.read_text(), 'protected')
        snapshot = self.backup / 'agents/test/reviewer.md'
        if snapshot.exists():
            receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
            self.assertEqual(snapshot.read_text(), 'local')
            displaced = Path(json.loads(receipt.read_text())['displaced'])
            if displaced.exists():
                self.assertEqual(displaced.read_text(), 'local')
            else:
                self.assertEqual((self.root / 'dest-moved' / 'reviewer.md').read_text(), 'local')
        else:
            self.assertEqual((self.root / 'dest-moved' / 'reviewer.md').read_text(), 'local')

    def test_backup_inside_destination_is_rejected_before_any_mutation(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('local')
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.root / 'config'), ('src',), ('test',),
                                     dry_run=False, adopt=True, backup_root=self.dest / 'backup')
        with self.assertRaisesRegex(ValueError, 'outside destination discovery'):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), 'local')
        self.assertFalse((self.dest / 'backup').exists())

    def test_root_swaps_at_each_publication_and_displacement(self):
        cases = [
            ('native-create', 'link'), ('raw-create', 'symlink'),
            ('native-adopt', 'rename'), ('native-adopt', 'link'),
            ('raw-adopt', 'rename'), ('raw-adopt', 'symlink'),
            ('legacy-adopt', 'rename'), ('legacy-adopt', 'link'), ('legacy-adopt', 'symlink'),
            ('refresh', 'rename'), ('refresh', 'link'), ('refresh', 'replace'),
        ]
        for mode, operation in cases:
            with self.subTest(mode=mode, operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'src' / 'agents'
                source.mkdir(parents=True)
                agent = source / 'reviewer.md'
                agent.write_text('---\nname: reviewer\ndescription: Review\n---\nOriginal.\n')
                protected = root / 'src' / 'skills'
                protected.mkdir()
                protected_file = protected / 'reviewer.md'
                protected_file.write_bytes(b'protected\r\n')
                dest = root / 'dest'
                dest.mkdir()
                raw = {'bridges': [{'name': 'src', 'path': str(source.parent), 'agents_path': 'agents'}],
                       'ai_agent_paths': {'test': str(dest)},
                       'sync': {'pull_before_sync': False, 'clone_if_missing': False}}
                if mode not in ('raw-create', 'raw-adopt'):
                    raw['agent_formats'] = {'test': 'omp'}
                def plan(adopt=False):
                    return build_agent_sync_plan(config_from_mapping(raw, root / 'config'), ('src',), ('test',),
                                                 dry_run=False, adopt=adopt, backup_root=root / 'backup')
                target = dest / 'reviewer.md'
                if mode == 'refresh':
                    apply_agent_sync_plan(plan())
                    before = target.read_bytes()
                    agent.write_text(agent.read_text() + 'update\n')
                elif mode == 'legacy-adopt':
                    target.symlink_to(agent)
                    before = str(agent)
                elif mode.endswith('adopt'):
                    target.write_text('local')
                    before = b'local'
                else:
                    before = None
                proposed = plan(adopt=True)
                self.assertFalse(proposed.has_blockers, proposed.errors)
                source_bytes = agent.read_bytes()
                import aiskillsync.agents as agents
                original = agents._rename_noreplace if operation == 'rename' else getattr(os, operation)
                real_rename, real_symlink = os.rename, os.symlink
                fired = False
                def swap(*args, **kwargs):
                    nonlocal fired
                    if not fired:
                        fired = True
                        real_rename(dest, root / 'dest-moved')
                        real_symlink(protected, dest, target_is_directory=True)
                    return original(*args, **kwargs)
                hook = 'aiskillsync.agents._rename_noreplace' if operation == 'rename' else f'aiskillsync.agents.os.{operation}'
                with patch(hook, side_effect=swap):
                    with self.assertRaises((ValueError, OSError)):
                        apply_agent_sync_plan(proposed)
                self.assertTrue(fired)
                self.assertEqual(protected_file.read_bytes(), b'protected\r\n')
                self.assertEqual(list(protected.iterdir()), [protected_file])
                self.assertEqual(agent.read_bytes(), source_bytes)
                if before is not None:
                    snapshot = root / 'backup/agents/test/reviewer.md'
                    self.assertTrue(snapshot.exists() or snapshot.is_symlink())
                    if mode == 'legacy-adopt':
                        self.assertEqual(os.readlink(snapshot), before)
                    else:
                        self.assertEqual(snapshot.read_bytes(), before)
                    receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
                    self.assertEqual(json.loads(receipt.read_text())['status'], 'incomplete')

    def test_absent_root_swap_at_mkdir_never_creates_inside_source(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        plan = self.plan()
        original = os.mkdir
        real_symlink = os.symlink
        fired = False
        def swap(path, *args, **kwargs):
            nonlocal fired
            if path == self.dest.name and kwargs.get('dir_fd') is not None and not fired:
                fired = True
                real_symlink(protected, self.dest, target_is_directory=True)
            return original(path, *args, **kwargs)
        with patch('aiskillsync.agents.os.mkdir', side_effect=swap):
            with self.assertRaises((OSError, ValueError)):
                apply_agent_sync_plan(plan)
        self.assertTrue(fired)
        self.assertEqual(list(protected.iterdir()), [])

    def test_configured_root_symlink_still_works(self):
        actual = self.root / 'actual'
        actual.mkdir()
        self.dest.symlink_to(actual, target_is_directory=True)
        apply_agent_sync_plan(self.plan())
        self.assertEqual((actual / 'reviewer.md').resolve(), self.agent)

    def test_unsupported_fd_operations_fail_before_writes(self):
        with patch('aiskillsync.agents._SUPPORTS_DIR_FD', False):
            with self.assertRaisesRegex(OSError, 'descriptor-relative'):
                apply_agent_sync_plan(self.plan())
        self.assertFalse(self.dest.exists())

    def test_moving_bound_root_inside_source_before_backup_parent_open(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        (self.dest / 'reviewer.md').write_text('local')
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        plan = self.plan(adopt=True)
        import aiskillsync.agents as agents
        real_open_dir, real_validate = agents._open_dir, agents._validate_bound
        fired = False
        ready = False
        validations = 0
        def validate(*args, **kwargs):
            nonlocal ready, validations
            result = real_validate(*args, **kwargs)
            if args[2] == 'reviewer.md':
                validations += 1
                if validations == 2:
                    ready = True
            return result
        def swap(path, *args, **kwargs):
            nonlocal fired
            if ready and not fired and path == self.dest.parent:
                fired = True
                self.dest.rename(protected / 'moved')
                self.dest.symlink_to(protected, target_is_directory=True)
            return real_open_dir(path, *args, **kwargs)
        with patch('aiskillsync.agents._validate_bound', side_effect=validate), \
                patch('aiskillsync.agents._open_dir', side_effect=swap):
            with self.assertRaises((OSError, ValueError)):
                apply_agent_sync_plan(plan)
        self.assertTrue(fired)
        self.assertEqual((protected / 'moved/reviewer.md').read_text(), 'local')
        self.assertEqual(sorted(p.name for p in protected.iterdir()), ['moved'])

    def test_nested_destination_backup_stays_outside_both_roots(self):
        outer = self.root / 'outer'
        self.dest = outer / 'inner'
        self.dest.mkdir(parents=True)
        (self.dest / 'reviewer.md').write_text('local')
        self.raw['ai_agent_paths'] = {'test': str(self.dest), 'outer': str(outer)}
        self.raw['agent_formats'] = {'test': 'omp'}
        config = config_from_mapping(self.raw, self.root / 'config')
        plan = build_agent_sync_plan(config, ('src',), ('test', 'outer'), dry_run=False,
                                     adopt=True, backup_root=self.backup)
        apply_agent_sync_plan(plan)
        snapshot = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        self.assertFalse(receipt.is_relative_to(outer))
        self.assertEqual(Path(json.loads(receipt.read_text())['displaced']).read_text(), 'local')
        self.assertEqual(list(outer.glob('.aiskillsync-agent-backup-*')), [])

    def test_backup_parent_swap_cannot_create_in_protected_source(self):
        import aiskillsync.agents as agents
        self.dest.mkdir()
        (self.dest / 'reviewer.md').write_text('local')
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        actual = self.root / 'backup-actual'
        actual.mkdir()
        alias = self.root / 'backup-alias'
        alias.symlink_to(actual, target_is_directory=True)
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.root / 'config'), ('src',), ('test',),
                                     dry_run=False, adopt=True, backup_root=alias)
        real_open = agents._open_dir
        fired = False
        def swap(path, *args, **kwargs):
            nonlocal fired
            if path == actual / 'agents/test' and not fired:
                fired = True
                actual.rename(self.root / 'backup-moved')
                actual.symlink_to(protected, target_is_directory=True)
            return real_open(path, *args, **kwargs)
        with patch('aiskillsync.agents._open_dir', side_effect=swap):
            with self.assertRaises((OSError, ValueError)):
                apply_agent_sync_plan(plan)
        self.assertTrue(fired)
        self.assertEqual(list(protected.iterdir()), [])
        self.assertEqual((self.dest / 'reviewer.md').read_text(), 'local')

    def test_independent_entry_in_private_displacement_slot_is_not_clobbered(self):
        self.dest.mkdir()
        (self.dest / 'reviewer.md').write_text('local')
        plan = self.plan(adopt=True)
        import aiskillsync.agents as agents
        original = agents._rename_noreplace
        fired = False
        def race(src, dst, *args, **kwargs):
            nonlocal fired
            if dst == 'displaced' and kwargs.get('dst_dir_fd') is not None:
                fired = True
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                             dir_fd=kwargs['dst_dir_fd'])
                os.write(fd, b'independent')
                os.close(fd)
            return original(src, dst, *args, **kwargs)
        with patch('aiskillsync.agents._rename_noreplace', side_effect=race):
            with self.assertRaises((ValueError, OSError)):
                apply_agent_sync_plan(plan)
        self.assertTrue(fired)
        displaced = list(self.root.glob('.aiskillsync-agent-backup-*/displaced'))
        self.assertEqual(len(displaced), 1)
        self.assertEqual(displaced[0].read_bytes(), b'independent')
        self.assertEqual((self.dest / 'reviewer.md').read_text(), 'local')

    def test_unsupported_exclusive_rename_fails_before_mutation(self):
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('local')
        plan = self.plan(adopt=True)
        with patch('aiskillsync.agents._atomic_rename_primitive', side_effect=OSError('unsupported rename')):
            with self.assertRaisesRegex(ValueError, 'unsupported rename'):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), 'local')
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.aiskillsync-agent-backup-*')), [])

    def test_prior_root_swap_during_later_action_is_incomplete(self):
        self.dest.mkdir()
        first = self.dest / 'reviewer.md'
        first.write_text('first local')
        second = self.root / 'second'
        second.mkdir()
        (second / 'reviewer.md').write_text('second local')
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        self.raw['ai_agent_paths']['second'] = str(second)
        config = config_from_mapping(self.raw, self.root / 'config')
        plan = build_agent_sync_plan(config, ('src',), ('test', 'second'), dry_run=False,
                                     adopt=True, backup_root=self.backup)
        real_symlink = os.symlink
        count = 0
        def race(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.dest.rename(self.root / 'dest-moved')
                real_symlink(protected, self.dest, target_is_directory=True)
            return real_symlink(*args, **kwargs)
        with patch('aiskillsync.agents.os.symlink', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                apply_agent_sync_plan(plan)
        self.assertEqual(list(protected.iterdir()), [])
        snapshot = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'incomplete')
        self.assertEqual(snapshot.read_text(), 'first local')

    def test_relocated_bound_root_under_source_at_publication_is_not_written(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        protected = self.root / 'src' / 'skills'
        protected.mkdir()
        plan = self.plan()
        real_link = os.link
        def swap(*args, **kwargs):
            self.dest.rename(protected / 'moved')
            self.dest.symlink_to(protected, target_is_directory=True)
            return real_link(*args, **kwargs)
        with patch('aiskillsync.agents.os.link', side_effect=swap):
            with self.assertRaises((ValueError, OSError)):
                apply_agent_sync_plan(plan)
        self.assertFalse((protected / 'moved' / 'reviewer.md').exists())

    def test_native_refresh_preserves_previous_bytes(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        apply_agent_sync_plan(self.plan())
        target = self.dest / 'reviewer.md'
        old = target.read_bytes()
        self.agent.write_text(self.agent.read_text() + 'Updated.\n')
        plan = self.plan()
        self.assertFalse(plan.has_blockers, plan.errors)
        self.assertEqual(plan.actions[0].action, 'refresh')
        apply_agent_sync_plan(plan)
        self.assertEqual((self.backup / 'agents' / 'test' / 'reviewer.md').read_bytes(), old)
        self.assertIn('Updated.', target.read_text())

    def test_omp_overlay_change_refreshes(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.raw['agent_overlays'] = {'test': {'reviewer': {'instructions': 'old instruction'}}}
        apply_agent_sync_plan(self.plan())
        target = self.dest / 'reviewer.md'
        previous = target.read_bytes()
        self.raw['agent_overlays']['test']['reviewer']['instructions'] = 'new instruction'
        apply_agent_sync_plan(self.plan())
        self.assertEqual((self.backup / 'agents/test/reviewer.md').read_bytes(), previous)
        self.assertIn('new instruction', target.read_text())

    def test_failure_after_publication_retains_incomplete_receipt(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('local')
        with patch('aiskillsync.agents.os.replace', side_effect=OSError('receipt failure')):
            with self.assertRaisesRegex(ValueError, 'incomplete.*reviewer.md.*receipt failure'):
                apply_agent_sync_plan(self.plan(adopt=True))
        self.assertIn('Review', target.read_text())
        snapshot = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        data = json.loads(receipt.read_text())
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(snapshot.read_text(), 'local')
        self.assertEqual(Path(data['displaced']).read_text(), 'local')

    def test_adoption_does_not_delete_replacement_at_move(self):
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('original')
        plan = self.plan(adopt=True)
        import aiskillsync.agents as agents
        real_rename = agents._rename_noreplace
        def race(src, dst, *args, **kwargs):
            if Path(src).name == target.name and kwargs.get('src_dir_fd') is not None:
                replacement = self.root / 'replacement'
                replacement.write_text('independent')
                os.replace(replacement, target)
            return real_rename(src, dst, *args, **kwargs)
        with patch('aiskillsync.agents._rename_noreplace', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                apply_agent_sync_plan(plan)
        self.assertEqual((self.backup / 'agents' / 'test' / 'reviewer.md').read_text(), 'original')
        displaced = list(self.root.glob('.aiskillsync-agent-backup-*/displaced'))
        self.assertEqual(len(displaced), 1)
        self.assertEqual(displaced[0].read_text(), 'independent')
        self.assertFalse(target.exists())

    def test_late_open_descriptor_write_keeps_both_versions(self):
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_bytes(b'original\n')
        fd = os.open(target, os.O_WRONLY)
        self.addCleanup(os.close, fd)
        apply_agent_sync_plan(self.plan(adopt=True))
        os.ftruncate(fd, 0)
        os.write(fd, b'late edit\n')
        saved = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((saved.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        data = json.loads(receipt.read_text())
        self.assertEqual(data['status'], 'complete')
        self.assertEqual(saved.read_bytes(), b'original\n')
        self.assertEqual(Path(data['displaced']).read_bytes(), b'late edit\n')
        self.assertTrue(target.is_symlink())

    def test_concurrent_publish_preserves_new_entry_and_incomplete_receipt(self):
        self.raw['agent_formats'] = {'test': 'omp'}
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('local')
        plan = self.plan(adopt=True)
        link = os.link
        def race(src, dst, *args, **kwargs):
            if Path(dst).name == target.name and kwargs.get('dst_dir_fd') is not None:
                target.write_text('independent')
            return link(src, dst, *args, **kwargs)
        with patch('aiskillsync.agents.os.link', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete.*backup'):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), 'independent')
        snapshot = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        data = json.loads(receipt.read_text())
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(snapshot.read_text(), 'local')
        self.assertEqual(Path(data['displaced']).read_text(), 'local')

    def test_abrupt_exit_after_move_leaves_receipt_and_both_backups(self):
        self.dest.mkdir()
        target = self.dest / 'reviewer.md'
        target.write_text('original')
        script = '''
import os, sys
from pathlib import Path
from aiskillsync.agents import build_agent_sync_plan, apply_agent_sync_plan
from aiskillsync.config import config_from_mapping
root=Path(sys.argv[1]); src=root/'src'; dest=root/'dest'
raw={'bridges':[{'name':'src','path':str(src),'agents_path':'agents'}], 'ai_agent_paths':{'test':str(dest)}, 'sync':{'pull_before_sync':False,'clone_if_missing':False}}
plan=build_agent_sync_plan(config_from_mapping(raw,root/'config'),('src',),('test',),dry_run=False,adopt=True,backup_root=root/'backup')
import aiskillsync.agents as agents
rename=agents._rename_noreplace
def die(a,b,*args,**kwargs):
    rename(a,b,*args,**kwargs); os._exit(41)
agents._rename_noreplace=die
apply_agent_sync_plan(plan)
'''
        proc = subprocess.run([sys.executable, '-c', script, str(self.root)], env=os.environ.copy())
        self.assertEqual(proc.returncode, 41)
        snapshot = self.backup / 'agents/test/reviewer.md'
        receipt = Path(json.loads((snapshot.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        data = json.loads(receipt.read_text())
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(snapshot.read_text(), 'original')
        self.assertEqual(Path(data['displaced']).read_text(), 'original')
        self.assertFalse(target.exists())

    def test_all_native_providers_refresh_and_explicitly_adopt_edits(self):
        for provider in ('omp', 'opencode', 'codex'):
            with self.subTest(provider=provider):
                self.dest = self.root / provider
                self.backup = self.root / ('backup-' + provider)
                self.raw['ai_agent_paths']['test'] = str(self.dest)
                self.raw['agent_formats'] = {'test': provider}
                apply_agent_sync_plan(self.plan())
                target = self.dest / ('reviewer.toml' if provider == 'codex' else 'reviewer.md')
                target.write_text(target.read_text() + 'local edit\n')
                edited = target.read_bytes()
                self.assertTrue(self.plan().has_blockers)
                apply_agent_sync_plan(self.plan(adopt=True))
                self.assertEqual((self.backup / 'agents/test' / target.name).read_bytes(), edited)
                self.assertNotIn('local edit', target.read_text())
                self.backup = self.root / ('refresh-' + provider)
                self.agent.write_text(self.agent.read_text() + provider + '\n')
                before = target.read_bytes()
                apply_agent_sync_plan(self.plan())
                self.assertEqual((self.backup / 'agents/test' / target.name).read_bytes(), before)
                self.assertIn(provider, target.read_text())

    def test_cli_agents_and_sync_all_refresh_and_explicit_adopt(self):
        from aiskillsync.config import config_to_text
        self.raw['agent_formats'] = {'test': 'omp'}
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config_from_mapping(self.raw, config_path)))
        home = self.root / 'home'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home)}
        def run(*args):
            return subprocess.run([sys.executable, '-m', 'aiskillsync', '--config', str(config_path), *args],
                                  env=env, text=True, capture_output=True)
        first = run('agents', '--dest', 'test')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        target = self.dest / 'reviewer.md'
        old = target.read_bytes()
        self.agent.write_text(self.agent.read_text() + 'next source\n')
        preview = run('sync', 'all', '--dry-run')
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn('REFRESH agent', preview.stdout)
        self.assertEqual(target.read_bytes(), old)
        applied = run('sync', 'all')
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn('Agent recovery index:', applied.stdout)
        self.assertIn('next source', target.read_text())
        target.write_text(target.read_text() + 'local rules\n')
        edited = target.read_bytes()
        denied = run('sync', 'all', '--overwrite')
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(target.read_bytes(), edited)
        adopted = run('agents', '--dest', 'test', '--adopt')
        self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
        self.assertIn('Agent recovery index:', adopted.stdout)
        snapshots = list((home / '.cache/aiskillsync-migration').glob('*/agents/test/reviewer.md'))
        self.assertEqual(len(snapshots), 2)
        self.assertIn(old, [p.read_bytes() for p in snapshots])
        self.assertIn(edited, [p.read_bytes() for p in snapshots])

    def test_sync_all_adopts_two_local_omp_files_and_two_legacy_links(self):
        from aiskillsync.config import config_to_text
        self.agent.unlink()
        self.dest.mkdir()
        self.raw['agent_formats'] = {'test': 'omp'}
        originals = {}
        for name in ('alpha', 'bravo', 'charlie', 'delta'):
            source = self.source / (name + '.md')
            source.write_text(f'---\nname: {name}\ndescription: {name} role\n---\ncanonical {name}\n')
            target = self.dest / (name + '.md')
            if name in ('alpha', 'bravo'):
                content = f'---\ntools: []\n---\nlocal {name}\n'
                target.write_text(content)
                originals[name] = content
            else:
                target.symlink_to(source)
                originals[name] = source
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config_from_mapping(self.raw, config_path)))
        home = self.root / 'home'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home)}
        run = lambda *args: subprocess.run([sys.executable, '-m', 'aiskillsync', '--config', str(config_path),
                                            'sync', 'all', *args], env=env, text=True, capture_output=True)
        self.assertNotEqual(run().returncode, 0)
        result = run('--adopt')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        roots = list((home / '.cache/aiskillsync-migration').glob('*/agents/test'))
        self.assertEqual(len(roots), 1)
        for name, original in originals.items():
            saved, target = roots[0] / (name + '.md'), self.dest / (name + '.md')
            self.assertIn('canonical ' + name, target.read_text())
            if isinstance(original, Path):
                self.assertTrue(saved.is_symlink())
                self.assertEqual(saved.readlink(), original)
            else:
                self.assertEqual(saved.read_text(), original)
        again = run()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn('No agent projections needed', again.stdout)
