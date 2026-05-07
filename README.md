# aiskillsync

Global AI skill synchronization tooling.

This repo is intended to replace per-project `sync_skills.sh` scripts with one
config-driven sync layer that can bridge skills from multiple repos into AI
provider skill directories.

## Personal migration helper

One-time helper for migrating Ryushe's current copied/symlinked Bounty Harness
skills into clean symlinks:

```bash
python3 scripts/migrate_aiskillsync_personal.py --list-sources
python3 scripts/migrate_aiskillsync_personal.py --backup-differs --apply
```

By default it processes Codex and Claude skill paths only. Ghost/OpenClaw is
available explicitly with `--dest ghost`.
