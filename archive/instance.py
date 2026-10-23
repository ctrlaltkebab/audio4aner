from __future__ import annotations
import os
from pathlib import Path


class InstanceLock:
    """OS-owned lock is released automatically, including after a crash."""
    def __init__(self, root):
        path = Path(root) / 'private' / 'instance.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('a+b')
        if path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
