from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def detect(path: Path, mountinfo: str, release: str = "5.15.0-microsoft-standard-WSL2") -> bool:
    from aiskillsync.windows_mount import is_windows_destination

    with patch("platform.system", return_value="Linux"), patch(
        "pathlib.Path.read_text", side_effect=[release, mountinfo]
    ):
        return is_windows_destination(path)


def mount(mountpoint: str, fs: str, source: str, options: str = "rw") -> str:
    return f"42 1 0:32 / {mountpoint} rw,relatime - {fs} {source} {options}\n"


class WindowsMountTests(unittest.TestCase):
    def test_virtiofs_detection_and_explicit_copy_override_in_sync(self) -> None:
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from aiskillsync.cli import main
        from aiskillsync.config import ConfigError, load_config
        from aiskillsync.sync import apply_sync_plan, build_sync_plan

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skill = base / "repo" / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("example")
            dest = base / "dest"
            config_file = base / "config.yaml"
            config_file.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
""")
            config = load_config(config_file)
            original_read = Path.read_text
            def read_with_mountinfo(path, *args, **kwargs):
                if path == Path("/proc/sys/kernel/osrelease"):
                    return "6.18.1-microsoft"
                if path == Path("/proc/self/mountinfo"):
                    return mount(str(dest), "virtiofs", tag)
                return original_read(path, *args, **kwargs)

            with patch("platform.system", return_value="Linux"), patch.object(Path, "read_text", read_with_mountinfo):
                tag = "hostshare"
                with self.assertRaises(ConfigError):
                    build_sync_plan(config, ("all",), (), dry_run=False)
                stderr = StringIO()
                with redirect_stdout(StringIO()), redirect_stderr(stderr):
                    result = main(["--config", str(config_file), "sync", "all"])
                self.assertEqual(result, 2)
                self.assertIn("sync.copy_destinations", stderr.getvalue())
                self.assertFalse(dest.exists())
                config_file.write_text(config_file.read_text() + "  copy_destinations: [windows]\n")
                explicit_config = load_config(config_file)
                explicit_plan = build_sync_plan(explicit_config, ("all",), (), dry_run=False)
                self.assertEqual(explicit_plan.copies[0].action, "copy-create")
                tag = "drvfsa"
                auto_plan = build_sync_plan(config, ("all",), (), dry_run=False)
                self.assertEqual(auto_plan.copies[0].action, "copy-create")
                apply_sync_plan(auto_plan)
            self.assertEqual((dest / "example" / "SKILL.md").read_text(), "example")
            self.assertFalse((dest / "example").is_symlink())

    def test_wsl_drvfs_mount_with_nondefault_path(self) -> None:
        from aiskillsync.windows_mount import is_windows_destination

        mountinfo = "42 1 0:32 / /media/windows rw,relatime - drvfs C:\\134 rw\n"
        with patch("platform.system", return_value="Linux"), patch(
            "pathlib.Path.read_text", side_effect=["5.15.0-microsoft-standard-WSL2", mountinfo]
        ):
            self.assertTrue(is_windows_destination(Path("/media/windows/new/skills")))

    def test_wsl_virtiofs_drvfsa_mount(self) -> None:
        entry = mount("/custom/windows", "virtiofs", "drvfsa")
        self.assertTrue(detect(Path("/custom/windows/Users/example"), entry))

    def test_wsl_container_virtiofs_drvfs_mount(self) -> None:
        entry = mount("/custom/windows", "virtiofs", "drvfs")
        self.assertTrue(detect(Path("/custom/windows/Users/example"), entry))

    def test_wsl2_9p_drvfs_mount(self) -> None:
        entry = mount("/mnt/c", "9p", r"C:\134", "rw,dirsync,aname=drvfs;path=C:\\;uid=1000")
        self.assertTrue(detect(Path("/mnt/c/Users/example/new"), entry))

    def test_nested_linux_mount_over_windows_is_not_windows(self) -> None:
        entries = mount("/mnt/c", "drvfs", "C:") + mount("/mnt/c/linux", "ext4", "/dev/sdb")
        self.assertFalse(detect(Path("/mnt/c/linux/new"), entries))

    def test_escaped_mountpoint_and_nonexistent_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            windows = base / "win space\\dir"
            windows.mkdir()
            link = base / "alias"
            link.symlink_to(windows, target_is_directory=True)
            escaped = str(windows).replace("\\", r"\134").replace(" ", r"\040")
            self.assertTrue(detect(link / "not-created" / "skills", mount(escaped, "drvfs", "D:")))

    def test_component_prefix_does_not_match_sibling(self) -> None:
        entries = mount("/", "ext4", "/dev/sda") + mount("/mnt/c", "drvfs", "C:")
        self.assertFalse(detect(Path("/mnt/c-extra/new"), entries))

    def test_unrecognized_9p_is_ambiguous(self) -> None:
        from aiskillsync.config import ConfigError

        with self.assertRaisesRegex(ConfigError, "sync.copy_destinations"):
            detect(Path("/mnt/share/new"), mount("/mnt/share", "9p", "hostshare", "rw,trans=virtio"))

    def test_unrecognized_virtiofs_is_ambiguous(self) -> None:
        from aiskillsync.config import ConfigError

        entry = mount("/mnt/share", "virtiofs", "hostshare")
        with self.assertRaisesRegex(ConfigError, "sync.copy_destinations"):
            detect(Path("/mnt/share/skills"), entry)

    def test_custom_wsl_kernel_with_distro_environment(self) -> None:
        entry = mount("/mnt/c", "virtiofs", "drvfsa")
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True):
            self.assertTrue(detect(Path("/mnt/c/skills"), entry, "6.18.1-custom"))

    def test_custom_wsl_kernel_with_interop_environment(self) -> None:
        entry = mount("/mnt/c", "9p", "drvfs")
        with patch.dict("os.environ", {"WSL_INTEROP": "/run/WSL/1_interop"}, clear=True):
            self.assertTrue(detect(Path("/mnt/c/skills"), entry, "6.18.1-custom"))

    def test_custom_wsl_kernel_with_binfmt_marker(self) -> None:
        entry = mount("/mnt/c", "virtiofs", "drvfsa")
        with patch.dict("os.environ", {}, clear=True), patch("os.path.exists", return_value=True) as exists:
            self.assertTrue(detect(Path("/mnt/c/skills"), entry, "6.18.1-custom"))
            exists.assert_called_once_with("/proc/sys/fs/binfmt_misc/WSLInterop")

    def test_unreadable_release_with_wsl_environment(self) -> None:
        from aiskillsync.windows_mount import is_windows_destination

        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Debian"}, clear=True), \
             patch("platform.system", return_value="Linux"), \
             patch("pathlib.Path.read_text", side_effect=[OSError("unreadable"), mount("/mnt/c", "drvfs", "C:")]):
            self.assertTrue(is_windows_destination(Path("/mnt/c/skills")))

    def test_non_wsl_is_not_windows(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch("os.path.exists", return_value=False):
            for fs, source in (("drvfs", "C:"), ("virtiofs", "drvfsa"), ("virtiofs", "hostshare")):
                with self.subTest(fs=fs, source=source):
                    self.assertFalse(detect(Path("/mnt/c/new"), mount("/mnt/c", fs, source), "6.8.0-generic"))

    def test_windows_mount_under_linux_mount_wins(self) -> None:
        entries = mount("/", "ext4", "/dev/sda") + mount("/custom/windows", "9p", "drvfs")
        self.assertTrue(detect(Path("/custom/windows/skills"), entries))

    def test_9p_drvfs_aname_must_be_exact(self) -> None:
        from aiskillsync.config import ConfigError

        entry = mount("/mnt/other", "9p", "drivers", "rw,aname=drvfs-other")
        with self.assertRaises(ConfigError):
            detect(Path("/mnt/other/skills"), entry)

    def test_wsl_linux_filesystem_is_not_windows(self) -> None:
        entries = mount("/", "ext4", "/dev/sda") + mount("/mnt/c", "9p", "C:", "rw,aname=drvfs")
        self.assertFalse(detect(Path("/home/user/skills"), entries))

    def test_stacked_mounts_are_ambiguous(self) -> None:
        from aiskillsync.config import ConfigError

        entries = mount("/mnt/c", "drvfs", "C:") + mount("/mnt/c", "ext4", "/dev/sdb")
        with self.assertRaisesRegex(ConfigError, "sync.copy_destinations"):
            detect(Path("/mnt/c/skills"), entries)

    def test_symlink_from_windows_to_linux_follows_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            windows = base / "windows"
            linux = base / "linux"
            windows.mkdir()
            linux.mkdir()
            (windows / "alias").symlink_to(linux, target_is_directory=True)
            entries = mount(str(base), "ext4", "/dev/sda") + mount(str(windows), "drvfs", "C:")
            self.assertFalse(detect(windows / "alias" / "missing", entries))

    def test_unreadable_mountinfo_reports_ambiguity(self) -> None:
        from aiskillsync.config import ConfigError
        from aiskillsync.windows_mount import is_windows_destination

        with patch("platform.system", return_value="Linux"), patch(
            "pathlib.Path.read_text", side_effect=["microsoft", PermissionError("unreadable")]
        ):
            with self.assertRaisesRegex(ConfigError, "sync.copy_destinations"):
                is_windows_destination(Path("/mnt/c/skills"))

    def test_unreadable_release_returns_false(self) -> None:
        from aiskillsync.windows_mount import is_windows_destination

        with patch.dict("os.environ", {}, clear=True), patch("os.path.exists", return_value=False), \
             patch("platform.system", return_value="Linux"), patch(
                 "pathlib.Path.read_text", side_effect=OSError("unreadable")
             ):
            self.assertFalse(is_windows_destination(Path("/mnt/c/skills")))

    def test_non_linux_returns_false_without_reading_proc(self) -> None:
        from aiskillsync.windows_mount import is_windows_destination

        with patch("platform.system", return_value="Windows"), patch("pathlib.Path.read_text") as read:
            self.assertFalse(is_windows_destination(Path("/mnt/c/skills")))
            read.assert_not_called()

    def test_invalid_mount_entries_report_ambiguity(self) -> None:
        from aiskillsync.config import ConfigError

        with self.assertRaisesRegex(ConfigError, "sync.copy_destinations"):
            detect(Path("/mnt/c/skills"), "garbage\n1 2 3 / /mnt/c - drvfs\n")

    def test_symlink_loop_reports_ambiguity(self) -> None:
        from aiskillsync.config import ConfigError
        from aiskillsync.windows_mount import is_windows_destination

        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "loop"
            link.symlink_to(link)
            with patch("platform.system", return_value="Linux"), patch(
                "pathlib.Path.read_text", side_effect=["microsoft", mount(tmp, "drvfs", "C:")]
            ):
                with self.assertRaises(ConfigError):
                    is_windows_destination(link / "skills")
