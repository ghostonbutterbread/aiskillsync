"""Regression coverage for the config parser's supported YAML indentation."""

import unittest
from pathlib import Path

from aiskillsync.config import ConfigError, config_from_mapping, config_to_text, parse_simple_yaml


class IndentlessSequenceTests(unittest.TestCase):
    def test_roundtrip_empty_destinations_and_colon_list_items(self) -> None:
        for raw in (
            {"sync": {"default_destinations": []}},
            {"sync": {"migration_denylist": ["foo:bar", "it's: valid"]}},
        ):
            with self.subTest(raw=raw):
                config = config_from_mapping(raw, Path("/synthetic/config.yaml"))
                restored = config_from_mapping(parse_simple_yaml(config_to_text(config)), config.path)
                self.assertEqual(restored, config)

    def test_legacy_config_roundtrips_tabs_and_quoted_backslashes(self) -> None:
        raw = {"repo_dir": r" /opt/team\tools' ",
               "bridges": [{"name": "team\tbridge", "path": "/tmp/repo"}],
               "ai_skill_paths": {"dest\tname": "/tmp/dest"}}
        config = config_from_mapping(raw, Path("/synthetic/config.yaml"))
        restored = config_from_mapping(parse_simple_yaml(config_to_text(config)), config.path)
        self.assertEqual(restored, config)

    def test_serializer_rejects_unrepresentable_values_and_keys(self) -> None:
        for raw in (
            {"repo_dir": "/repo\u2028hidden: value"},
            {"ai_skill_paths": {"dest: hidden": "/tmp/dest"}},
        ):
            with self.subTest(raw=raw):
                config = config_from_mapping(raw, Path("/synthetic/config.yaml"))
                with self.assertRaises(ConfigError):
                    config_to_text(config)

    def test_legacy_double_quoted_backslashes_remain_literal(self) -> None:
        for value in (r"/opt/team\tools", r"/opt/\qual"):
            with self.subTest(value=value):
                parsed = parse_simple_yaml(f'repo_dir: "{value}"\n')
                self.assertEqual(parsed["repo_dir"], value)

    def test_overlay_surrogate_pair_decodes_but_isolated_surrogates_are_rejected(self) -> None:
        prefix = "agent_overlays:\n  dest:\n    role:\n      instructions: "
        parsed = parse_simple_yaml(prefix + r'"valid \ud83d\ude00"' + "\n")
        self.assertEqual(parsed["agent_overlays"]["dest"]["role"]["instructions"], "valid 😀")
        for code in (r"\ud800", r"\udc00"):
            with self.subTest(code=code), self.assertRaisesRegex(ConfigError, "surrogate"):
                parse_simple_yaml(prefix + '"' + code + '"\n')

    def test_legacy_single_quotes_and_unterminated_scalars_keep_prior_semantics(self) -> None:
        self.assertEqual(parse_simple_yaml("value: 'a'b'\n"), {"value": "a'b"})
        self.assertEqual(parse_simple_yaml('value: "unfinished\n'), {"value": '"unfinished'})

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
