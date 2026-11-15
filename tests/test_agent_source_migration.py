from pathlib import Path
import tempfile
import unittest
import os
import subprocess
import sys
import json
from unittest.mock import patch

from aiskillsync.config import ConfigError, config_from_mapping, config_to_text, parse_simple_yaml
from aiskillsync.agents import build_agent_sync_plan, apply_agent_sync_plan


class AgentSourceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = {'bridges': [{'name': 'src', 'path': str(self.root / 'src'), 'agents_path': 'agents'}],
                    'ai_agent_paths': {'omp': str(self.root / 'dest')},
                    'agent_formats': {'omp': 'omp'},
                    'agent_overlays': {'omp': {'reviewer': {'frontmatter': {'model': 'provider/model:max', 'thinkingLevel': 'max'}}}},
                    'sync': {'pull_before_sync': False, 'clone_if_missing': False}}

    def test_omp_model_valid_and_config_roundtrip(self):
        config = config_from_mapping(self.raw, self.root / 'config')
        self.assertEqual(config_from_mapping(parse_simple_yaml(config_to_text(config)), self.root / 'config'), config)

    def test_model_rejects_invalid_values(self):
        for value in ('', '  ', 'bad\nmodel', 'bad\rmodel', 17, None, [], 'bad\u2028model'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.raw['agent_overlays']['omp']['reviewer']['frontmatter']['model'] = value
                config_from_mapping(self.raw, self.root / 'config')

    def fixture(self):
        old, new = self.root / 'old/agents', self.root / 'src/agents'
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        content = '---\nname: reviewer\ndescription: Review\n---\nPrompt\n'
        (old / 'reviewer.md').write_text(content)
        (new / 'reviewer.md').write_text(content)
        old_raw = dict(self.raw)
        old_raw['bridges'] = [{'name': 'src', 'path': str(old.parent), 'agents_path': 'agents'}]
        previous = config_from_mapping(old_raw, self.root / 'config')
        apply_agent_sync_plan(build_agent_sync_plan(previous, ('src',), ('omp',), dry_run=False))
        return old, config_from_mapping(self.raw, self.root / 'config'), self.root / 'dest/reviewer.md'

    def test_cli_migration_dry_run_and_apply(self):
        old, config, target = self.fixture()
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config))
        env = {**os.environ, 'HOME': str(self.root / 'home')}
        cmd = [sys.executable, '-m', 'aiskillsync', '--config', str(config_path), 'agents', '--dest', 'omp', '--former-agent-source', f'src={old}']
        before = target.read_bytes()
        preview = subprocess.run(cmd + ['--dry-run'], env=env, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertEqual(target.read_bytes(), before)
        applied = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        backups = list((self.root / 'home/.cache/aiskillsync-migration').glob('*/agents/omp/reviewer.md'))
        self.assertEqual([p.read_bytes() for p in backups], [before])
        self.assertEqual(subprocess.run(cmd, env=env, capture_output=True).returncode, 0)

    def test_unknown_edits_and_stale_unrelated_entries_block_even_with_adopt(self):
        old, config, target = self.fixture()
        target.write_text(target.read_text() + 'unreviewed\n')
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, adopt=True, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        self.assertIn('body differs', ' '.join(plan.errors))
        self.assertEqual(target.read_bytes(), before)

    def test_former_source_change_after_plan_is_rejected(self):
        old, config, target = self.fixture()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        before = target.read_bytes()
        (old / 'reviewer.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'former agent source changed'):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), before)

    def test_former_source_migration_preserves_exact_reviewed_overlay_and_backup(self):
        old = self.root / 'old' / 'agents'
        new = self.root / 'src' / 'agents'
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        content = '---\nname: reviewer\ndescription: Review\n---\nPrompt\n'
        (old / 'reviewer.md').write_text(content)
        (new / 'reviewer.md').write_text(content)
        config = config_from_mapping(self.raw, self.root / 'config')
        previous_raw = dict(self.raw)
        previous_raw['bridges'] = [{'name': 'src', 'path': str(old.parent), 'agents_path': 'agents'}]
        previous_config = config_from_mapping(previous_raw, self.root / 'config')
        apply_agent_sync_plan(build_agent_sync_plan(previous_config, ('src',), ('omp',), dry_run=False))
        target = self.root / 'dest' / 'reviewer.md'
        previous = target.read_bytes()
        self.assertTrue(build_agent_sync_plan(config, ('src',), ('omp',), dry_run=True).has_blockers)
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False,
                                     former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        self.assertFalse(plan.has_blockers, plan.errors)
        apply_agent_sync_plan(plan)
        self.assertEqual((self.root / 'backup/agents/omp/reviewer.md').read_bytes(), previous)
        self.assertIn('provider/model:max', target.read_text())
        self.assertEqual(build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False).actions[0].action, 'skip')

    def test_former_source_nested_inside_current_source_is_rejected(self):
        old, config, target = self.fixture()
        nested = self.root / 'src/agents/nested'
        nested.mkdir()
        with self.assertRaisesRegex(ConfigError, 'overlap'):
            build_agent_sync_plan(config, ('src',), ('omp',), dry_run=True, former_agent_sources={'src': nested})

    def test_modified_model_with_original_marker_migrates_only_when_overlay_matches(self):
        saved_overlay = self.raw.pop('agent_overlays')
        old, _, target = self.fixture()
        baseline = target.read_text()
        self.raw['agent_overlays'] = saved_overlay
        config = config_from_mapping(self.raw, self.root / 'config')
        old_raw = dict(self.raw)
        old_raw['bridges'] = [{'name': 'src', 'path': str(old.parent), 'agents_path': 'agents'}]
        expected_config = config_from_mapping(old_raw, self.root / 'config')
        rendered = build_agent_sync_plan(expected_config, ('src',), ('omp',), dry_run=True).actions[0].content
        lines = rendered.splitlines(keepends=True)
        lines[1] = baseline.splitlines()[1] + '\n'
        target.write_text(''.join(lines))
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        self.assertFalse(plan.has_blockers, plan.errors)
        apply_agent_sync_plan(plan)
        self.assertEqual((self.root / 'backup/agents/omp/reviewer.md').read_bytes(), before)

    def test_migration_partial_publication_failure_keeps_incomplete_receipt(self):
        old, config, target = self.fixture()
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        with patch('aiskillsync.agents.os.replace', side_effect=OSError('simulated failure')):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                apply_agent_sync_plan(plan)
        saved = self.root / 'backup/agents/omp/reviewer.md'
        self.assertEqual(saved.read_bytes(), before)
        receipt = Path(json.loads((saved.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'incomplete')

    def test_unrelated_stale_managed_file_blocks_migration(self):
        old, config, target = self.fixture()
        (target.parent / 'unrelated.md').write_text(target.read_text())
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        with self.assertRaisesRegex(ValueError, 'blockers'):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), before)

    def test_migration_opt_in_with_adopt_never_discards_unrecognized_content(self):
        old, config, target = self.fixture()
        target.write_text('unrecognized local content\n')
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False,
                                     adopt=True, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), before)

    def test_migration_rejects_unrelated_marker_content_digest(self):
        old, config, target = self.fixture()
        lines = target.read_text().splitlines(keepends=True)
        parts = lines[1].split()
        parts[-1] = '0' * 64
        lines[1] = ' '.join(parts) + '\n'
        target.write_text(''.join(lines))
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)

    def test_new_stale_entry_after_planning_blocks_before_migration(self):
        old, config, target = self.fixture()
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        (target.parent / 'stale.md').write_bytes(before)
        with self.assertRaisesRegex(ValueError, 'destination.*changed'):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), before)

    def test_former_source_change_at_publication_reports_incomplete(self):
        old, config, target = self.fixture()
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        link = os.link
        def race(*args, **kwargs):
            result = link(*args, **kwargs)
            (old / 'reviewer.md').write_text('changed during publication')
            return result
        with patch('aiskillsync.agents.os.link', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete.*former agent source changed'):
                apply_agent_sync_plan(plan)
        self.assertEqual((self.root / 'backup/agents/omp/reviewer.md').read_bytes(), before)

    def test_former_source_cannot_overlap_other_configured_source(self):
        old, _, _ = self.fixture()
        other = self.root / 'other' / 'agents'
        other.mkdir(parents=True)
        self.raw['bridges'].append({'name': 'other', 'path': str(other.parent), 'agents_path': 'agents'})
        config = config_from_mapping(self.raw, self.root / 'config')
        with self.assertRaisesRegex(ConfigError, 'overlap'):
            build_agent_sync_plan(config, ('src',), ('omp',), dry_run=True, former_agent_sources={'src': other})

    def test_migrates_all_native_providers_without_cross_binding(self):
        old, config, target = self.fixture()
        self.raw['ai_agent_paths'].update({'codex': str(self.root / 'codex'), 'opencode': str(self.root / 'opencode')})
        self.raw['agent_formats'].update({'codex': 'codex', 'opencode': 'opencode'})
        old_raw = dict(self.raw)
        old_raw['bridges'] = [{'name': 'src', 'path': str(old.parent), 'agents_path': 'agents'}]
        previous_config = config_from_mapping(old_raw, self.root / 'config')
        apply_agent_sync_plan(build_agent_sync_plan(previous_config, ('src',), ('codex', 'opencode'), dry_run=False))
        config = config_from_mapping(self.raw, self.root / 'config')
        before = {dest: (self.root / dest / ('reviewer.toml' if dest == 'codex' else 'reviewer.md')).read_bytes()
                  for dest in ('codex', 'opencode')}
        plan = build_agent_sync_plan(config, ('src',), ('codex', 'opencode'), dry_run=False,
                                     former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        self.assertFalse(plan.has_blockers, plan.errors)
        apply_agent_sync_plan(plan)
        for dest, data in before.items():
            name = 'reviewer.toml' if dest == 'codex' else 'reviewer.md'
            self.assertEqual((self.root / 'backup/agents' / dest / name).read_bytes(), data)
        self.assertTrue(all(action.action == 'skip' for action in build_agent_sync_plan(config, ('src',), ('codex', 'opencode'), dry_run=False).actions))

    def test_invalid_former_sources_and_wrong_bindings_are_rejected(self):
        old, config, target = self.fixture()
        for mapping in ({'missing': old}, {'src': Path('relative')}, {'src': self.root / 'missing'}):
            with self.subTest(mapping=mapping), self.assertRaises(ConfigError):
                build_agent_sync_plan(config, ('src',), ('omp',), dry_run=True, former_agent_sources=mapping)
        before = target.read_bytes()
        (old / 'reviewer.md').write_text('---\nname: someone-else\ndescription: Review\n---\nPrompt\n')
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        self.assertEqual(target.read_bytes(), before)

    def test_migration_destination_edit_after_plan_is_not_discarded(self):
        old, config, target = self.fixture()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, former_agent_sources={'src': old})
        target.write_text(target.read_text() + 'concurrent edit\n')
        edited = target.read_bytes()
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_bytes(), edited)

    def test_raw_migration_adopt_requires_exact_former_link(self):
        old, _, target = self.fixture()
        self.raw['agent_formats'] = {}
        self.raw.pop('agent_overlays')
        config = config_from_mapping(self.raw, self.root / 'config')
        target.unlink()
        target.write_text('unrelated local file')
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, adopt=True, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        self.assertEqual(target.read_text(), 'unrelated local file')
        target.unlink()
        other = self.root / 'other.md'
        other.write_text('unrelated')
        target.symlink_to(other)
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, adopt=True, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers)
        self.assertEqual(target.readlink(), other)
        target.unlink()
        target.symlink_to(old / 'reviewer.md')
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, adopt=True, former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        self.assertFalse(plan.has_blockers, plan.errors)
        apply_agent_sync_plan(plan)
        self.assertEqual(target.resolve(), self.root / 'src/agents/reviewer.md')
        self.assertEqual((self.root / 'backup/agents/omp/reviewer.md').readlink(), old / 'reviewer.md')

    def test_raw_source_edit_during_publication_is_incomplete(self):
        old, _, target = self.fixture()
        self.raw['agent_formats'] = {}
        self.raw.pop('agent_overlays')
        config = config_from_mapping(self.raw, self.root / 'config')
        target.unlink()
        target.symlink_to(old / 'reviewer.md')
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False, adopt=True,
                                     former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        symlink = os.symlink
        def race(*args, **kwargs):
            result = symlink(*args, **kwargs)
            if args[0] == str(self.root / 'src/agents/reviewer.md'):
                (self.root / 'src/agents/reviewer.md').write_text('changed source')
            return result
        with patch('aiskillsync.agents.os.symlink', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete.*source changed'):
                apply_agent_sync_plan(plan)
        backup = self.root / 'backup/agents/omp/reviewer.md'
        self.assertEqual(backup.readlink(), old / 'reviewer.md')
        receipt = Path(json.loads((backup.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'incomplete')

    def test_cli_migration_never_pulls_after_preview(self):
        old, _, target = self.fixture()
        source = self.root / 'src'
        subprocess.run(['git', 'init', '-b', 'main', str(source)], check=True, capture_output=True)
        def commit(repo, msg):
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid', 'commit', '-m', msg], check=True, capture_output=True)
        commit(source, 'initial')
        checkout = self.root / 'checkout'
        subprocess.run(['git', 'clone', '--branch', 'main', str(source), str(checkout)], check=True, capture_output=True)
        self.raw['bridges'][0].update(path=str(checkout), repo=source.as_uri(), branch='main')
        self.raw['sync'].update(pull_before_sync=True, clone_if_missing=True)
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config_from_mapping(self.raw, config_path)))
        cmd = [sys.executable, '-m', 'aiskillsync', '--config', str(config_path), 'agents', '--dest', 'omp', '--former-agent-source', f'src={old}']
        env = {**os.environ, 'HOME': str(self.root / 'home'), 'GIT_TERMINAL_PROMPT': '0'}
        preview = subprocess.run(cmd + ['--dry-run'], env=env, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        (source / 'agents/reviewer.md').write_text('---\nname: reviewer\ndescription: Review\n---\nNEW UNREVIEWED CONTENT\n')
        commit(source, 'unreviewed')
        applied = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertNotIn('NEW UNREVIEWED CONTENT', target.read_text())
        self.assertNotIn('NEW UNREVIEWED CONTENT', (checkout / 'agents/reviewer.md').read_text())

    def test_cli_rejects_relative_former_before_clone(self):
        old, _, target = self.fixture()
        source = self.root / 'src'
        subprocess.run(['git', 'init', '-b', 'main', str(source)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid', 'commit', '-m', 'initial'], check=True, capture_output=True)
        checkout = self.root / 'missing-checkout'
        self.raw['bridges'][0].update(path=str(checkout), repo=source.as_uri(), branch='main')
        self.raw['sync'].update(pull_before_sync=True, clone_if_missing=True)
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config_from_mapping(self.raw, config_path)))
        cmd = [sys.executable, '-m', 'aiskillsync', '--config', str(config_path), 'agents', '--dest', 'omp', '--former-agent-source', 'src=relative']
        result = subprocess.run(cmd, env={**os.environ, 'HOME': str(self.root / 'home')}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(checkout.exists(), result.stdout + result.stderr)

    def test_cli_migration_refuses_missing_checkout_without_clone(self):
        old, _, target = self.fixture()
        source = self.root / 'src'
        subprocess.run(['git', 'init', '-b', 'main', str(source)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid', 'commit', '-m', 'initial'], check=True, capture_output=True)
        checkout = self.root / 'missing-checkout'
        self.raw['bridges'][0].update(path=str(checkout), repo=source.as_uri(), branch='main')
        self.raw['sync'].update(pull_before_sync=True, clone_if_missing=True)
        config_path = self.root / 'config.yaml'
        config_path.write_text(config_to_text(config_from_mapping(self.raw, config_path)))
        cmd = [sys.executable, '-m', 'aiskillsync', '--config', str(config_path), 'agents', '--dest', 'omp', '--former-agent-source', f'src={old}']
        env = {**os.environ, 'HOME': str(self.root / 'home')}
        before = target.read_bytes()
        for extra in (['--dry-run'], []):
            result = subprocess.run(cmd + extra, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(checkout.exists(), result.stdout + result.stderr)
            self.assertEqual(target.read_bytes(), before)

    def test_unselected_stale_former_symlink_blocks_migration(self):
        old, config, target = self.fixture()
        stale = target.parent / 'unselected.md'
        stale.symlink_to(old / 'deleted.md')
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False,
                                     include_agents=('reviewer',), adopt=True, former_agent_sources={'src': old})
        self.assertTrue(plan.has_blockers, plan.errors)
        with self.assertRaisesRegex(ValueError, 'blockers'):
            apply_agent_sync_plan(plan)
        self.assertEqual(stale.readlink(), old / 'deleted.md')
        self.assertEqual(target.read_bytes(), before)

    def test_destination_edit_during_publication_keeps_incomplete_receipt(self):
        old, config, target = self.fixture()
        before = target.read_bytes()
        plan = build_agent_sync_plan(config, ('src',), ('omp',), dry_run=False,
                                     former_agent_sources={'src': old}, backup_root=self.root / 'backup')
        link = os.link
        def race(*args, **kwargs):
            result = link(*args, **kwargs)
            target.write_text('concurrent user edit')
            return result
        with patch('aiskillsync.agents.os.link', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'incomplete.*destination changed'):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), 'concurrent user edit')
        saved = self.root / 'backup/agents/omp/reviewer.md'
        self.assertEqual(saved.read_bytes(), before)
        receipt = Path(json.loads((saved.parent / 'reviewer.md.receipt.json').read_text())['receipt'])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'incomplete')
