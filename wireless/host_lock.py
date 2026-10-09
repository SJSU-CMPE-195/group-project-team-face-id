"""One process owns a product database, including offline maintenance."""

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def database_process_lock(database_path: Path):
    database_path = Path(database_path).expanduser().resolve()
    lock_path = Path(f"{database_path}.host.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if lock_path.is_symlink():
        raise RuntimeError("The product process lock must not be a symlink.")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    locked = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "This product database is in use. Stop the BASS host "
                "(faceid-wireless.service on Pi) before offline maintenance."
            ) from exc
        locked = True
        from .developer_reset import protect_private_file

        protect_private_file(lock_path)
        yield
    finally:
        try:
            if locked:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
