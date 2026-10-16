"""Regression coverage for the config parser's supported YAML indentation."""

import unittest
from pathlib import Path

from aiskillsync.config import ConfigError, config_from_mapping, parse_simple_yaml


class IndentlessSequenceTests(unittest.TestCase):
    def test_indentless_sequences_match_indented_config(self) -> None:
        # PyYAML safe_dump's default formatting places sequence markers at the
        # same indentation as the mapping key that owns them.
        indentless = """repo_dir: /synthetic/repos
bridges:
- name: synthetic
  repo: https://example.invalid/synthetic.git
  path: /synthetic/checkout
  skills_path: skills
  branch: beta
  enabled: true
ai_skill_paths:
  codex: /synthetic/dest
sync:
  default_destinations:
  - codex
"""
        indented = """repo_dir: /synthetic/repos
bridges:
  - name: synthetic
    repo: https://example.invalid/synthetic.git
    path: /synthetic/checkout
    skills_path: skills
    branch: beta
    enabled: true
ai_skill_paths:
  codex: /synthetic/dest
sync:
  default_destinations:
    - codex
"""
        parsed = parse_simple_yaml(indentless)
        self.assertEqual(parsed, parse_simple_yaml(indented))
        config = config_from_mapping(parsed, Path("/synthetic/config.yaml"))
        self.assertEqual(config.bridges[0].name, "synthetic")
        self.assertEqual(config.sync.default_destinations, ("codex",))

    def test_nested_profile_sequences_at_key_indent(self) -> None:
        parsed = parse_simple_yaml("""bridges:
- name: synthetic
  path: /synthetic/checkout
  skill_layout: categorized
ai_skill_paths:
  codex: /synthetic/dest
profile_sets:
  focused:
    destination: codex
    sources:
    - bridge: synthetic
      include_categories:
      - security
""")
        config = config_from_mapping(parsed, Path("/synthetic/config.yaml"))
        self.assertEqual(config.profile_sets[0].sources[0].include_categories, ("security",))

    def test_sequence_after_populated_scalar_is_not_accepted(self) -> None:
        with self.assertRaisesRegex(ConfigError, "could not parse config near line 2"):
            parse_simple_yaml("bridges: []\n- name: synthetic\n")

    def test_sequence_does_not_absorb_sibling_mapping(self) -> None:
        parsed = parse_simple_yaml("bridges:\n- name: synthetic\nother: value\n")
        self.assertEqual(parsed, {"bridges": [{"name": "synthetic"}], "other": "value"})


if __name__ == "__main__":
    unittest.main()
