import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

root = Path(tempfile.mkdtemp(prefix='managed-race-', dir=os.environ['TMPDIR']))
os.chmod(root, 0o700)
print('fixture:', root, flush=True)

# Existing raw adoption: pathname is swapped at the final unlink after the
# backup and identity checks, as an uncooperative editor could do.
from aiskillsync.agents import _move_no_clobber
case = root / 'current'
case.mkdir()
target, backup = case / 'agent.md', case / 'backup'
target.write_text('planned old bytes')
original_unlink = Path.unlink
def racing_unlink(path, *args, **kwargs):
    if path == target:
        replacement = case / 'replacement'
        replacement.write_text('concurrent user bytes')
        os.replace(replacement, target)
    return original_unlink(path, *args, **kwargs)
with patch.object(Path, 'unlink', racing_unlink):
    _move_no_clobber(target, backup)
print('current adoption:', 'backup=', repr(backup.read_text()), 'target_exists=', target.exists(), 'replacement_exists=', (case/'replacement').exists())
assert backup.read_text() == 'planned old bytes' and not target.exists()

# Candidate stopped-writer protocol under a writer holding an old fd. Parent
# renames and verifies, publishes, then releases the already-open child writer.
case = root / 'open-fd'
case.mkdir()
target = case / 'agent.md'
target.write_text('original managed bytes')
private = case / '.private-backup'
private.mkdir(mode=0o700)
backup = private / 'agent.md'
writer = subprocess.Popen([sys.executable, '-u', '-c', '''import os,sys
fd = os.open(sys.argv[1], os.O_WRONLY)
print("opened", flush=True)
sys.stdin.readline()
os.ftruncate(fd, 0)
os.write(fd, b"late user replacement")
os.close(fd)
print("wrote", flush=True)
''', str(target)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert writer.stdout.readline().strip() == 'opened'
os.rename(target, backup)
assert backup.read_text() == 'original managed bytes'
new = case / 'new'
new.write_text('fresh managed bytes')
os.link(new, target)
writer.stdin.write('go\n'); writer.stdin.flush()
assert writer.stdout.readline().strip() == 'wrote'
assert writer.wait() == 0
print('open-fd after reported success:', 'target=', repr(target.read_text()), 'backup=', repr(backup.read_text()), 'original_backup_retained=', backup.read_text() == 'original managed bytes')
assert target.read_text() == 'fresh managed bytes' and backup.read_text() == 'late user replacement'

# Concurrent replacement right before move can be quarantined and detected;
# publication via link must not clobber an independently created target.
case = root / 'path-races'
case.mkdir()
target = case / 'agent.md'
target.write_text('planned old bytes')
planned = target.lstat().st_ino
replacement = case / 'replacement'
replacement.write_text('other user bytes')
os.replace(replacement, target)
private = case / '.private-backup'
private.mkdir(mode=0o700)
backup = private / 'agent.md'
os.rename(target, backup)
assert backup.lstat().st_ino != planned
assert backup.read_text() == 'other user bytes'
target.write_text('new concurrent path')
new = case / 'new'
new.write_text('generated')
try:
    os.link(new, target)
except FileExistsError:
    pass
else:
    raise AssertionError('exclusive publication clobbered concurrent target')
assert target.read_text() == 'new concurrent path'
print('path-races:', 'quarantined unexpected=', repr(backup.read_text()), 'concurrent target=', repr(target.read_text()), 'exclusive publish refused')

# Crash after rename leaves backup but no active target; show need for recovery.
case = root / 'crash'
case.mkdir()
target = case / 'agent.md'
target.write_text('recover me')
private = case / '.private-backup'
private.mkdir(mode=0o700)
backup = private / 'agent.md'
child = subprocess.run([sys.executable, '-c', 'import os,sys; os.rename(sys.argv[1],sys.argv[2]); os._exit(42)', str(target), str(backup)])
assert child.returncode == 42 and not target.exists() and backup.read_text() == 'recover me'
print('crash after rename:', 'exit=', child.returncode, 'target_exists=', target.exists(), 'backup=', repr(backup.read_text()))
print('all assertions passed')
