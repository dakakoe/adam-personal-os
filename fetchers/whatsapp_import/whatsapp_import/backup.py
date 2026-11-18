"""Finding WhatsApp's message store inside an iPhone local backup.

A Finder/iTunes backup is a flat pile of files named by hash, with a SQLite
index (Manifest.db) mapping (domain, relative path) to the hash. So getting at
ChatStorage.sqlite is: read the index, compute the hash, open that file.

Nothing here talks to WhatsApp or to the network. It reads a backup that is
already on this Mac.
"""

from __future__ import annotations

import hashlib
import plistlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

# WhatsApp keeps its message store in the shared app-group container.
WA_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
CHATSTORAGE = "ChatStorage.sqlite"

DEFAULT_BACKUP_ROOT = Path.home() / "Library/Application Support/MobileSync/Backup"


class BackupError(RuntimeError):
    """Anything that means we cannot read this backup, with a fix in the text."""


def manifest_file_id(domain: str, relative_path: str) -> str:
    """The hashed filename a backup stores a file under.

    Pure and trivially checkable: SHA-1 of "domain-relativePath". The file then
    lives at <backup>/<first two hex chars>/<full hash>.
    """
    return hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()


def hashed_path(backup_dir: Path, file_id: str) -> Path:
    return backup_dir / file_id[:2] / file_id


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    device_name: str | None
    last_backup: str | None
    encrypted: bool


def _read_plist(p: Path) -> dict:
    try:
        with p.open("rb") as fh:
            return plistlib.load(fh)
    except FileNotFoundError:
        return {}
    except PermissionError:
        raise
    except Exception:
        return {}


def describe(backup_dir: Path) -> BackupInfo:
    info = _read_plist(backup_dir / "Info.plist")
    manifest = _read_plist(backup_dir / "Manifest.plist")
    last = info.get("Last Backup Date")
    return BackupInfo(
        path=backup_dir,
        device_name=info.get("Device Name") or info.get("Product Name"),
        last_backup=str(last) if last else None,
        encrypted=bool(manifest.get("IsEncrypted")),
    )


def list_backups(root: Path = DEFAULT_BACKUP_ROOT) -> list[BackupInfo]:
    """Every backup on this Mac, newest-looking first.

    The backup directory is protected by macOS privacy controls, so the most
    likely failure here is not "no backups" but "not allowed to look", and the
    two must not be confused.
    """
    if not root.exists():
        raise BackupError(
            f"No backup folder at {root}.\n"
            "Connect the iPhone, open Finder, select the device, choose\n"
            '"Back up all of the data on your iPhone to this Mac", make sure\n'
            '"Encrypt local backup" is UNCHECKED, then Back Up Now.'
        )
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except PermissionError as e:
        raise BackupError(_fda_message(root)) from e

    out = [describe(d) for d in dirs]
    out.sort(key=lambda b: b.last_backup or "", reverse=True)
    return out


def _fda_message(root: Path) -> str:
    return (
        f"macOS blocked reading {root}.\n\n"
        "That folder needs Full Disk Access. Grant it to the app you are\n"
        "running this from:\n"
        "  System Settings > Privacy & Security > Full Disk Access\n"
        "  turn it on for Terminal (or iTerm, or Claude), then RESTART that app.\n\n"
        "This is a permission problem, not a missing backup."
    )


def pick_backup(explicit: Path | None = None, root: Path = DEFAULT_BACKUP_ROOT) -> BackupInfo:
    """The backup to import from: the one named, else the most recent."""
    if explicit:
        d = Path(explicit).expanduser()
        if not d.is_dir():
            raise BackupError(f"Not a backup folder: {d}")
        return describe(d)

    backups = list_backups(root)
    if not backups:
        raise BackupError(
            f"No backups found under {root}.\n"
            "Take one in Finder first, with encryption switched OFF."
        )
    return backups[0]


def require_usable(info: BackupInfo) -> None:
    """Refuse an encrypted backup, loudly and with the actual fix.

    An encrypted backup cannot be reused by turning encryption off — the
    existing one stays encrypted. A fresh backup has to be taken.
    """
    if info.encrypted:
        raise BackupError(
            f"This backup is encrypted, so its files cannot be read:\n  {info.path}\n\n"
            "Encryption cannot be removed from a backup that already exists.\n"
            "In Finder, select the iPhone, UNCHECK 'Encrypt local backup',\n"
            "then take a NEW backup and run this again.\n\n"
            "Note the tradeoff: an unencrypted backup on this Mac is readable\n"
            "by anything with disk access, including this importer. Consider\n"
            "re-enabling encryption once the import is done."
        )


def find_chatstorage(backup_dir: Path) -> Path:
    """Locate ChatStorage.sqlite via Manifest.db, falling back to the raw hash.

    Manifest.db is authoritative because a file can be recorded under a path
    we did not predict, but on some backups it is itself unreadable, so the
    computed hash is tried as a backstop.
    """
    manifest = backup_dir / "Manifest.db"
    if manifest.exists():
        try:
            con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
                    (WA_DOMAIN, CHATSTORAGE),
                ).fetchone()
            finally:
                con.close()
            if row:
                p = hashed_path(backup_dir, row[0])
                if p.exists():
                    return p
        except PermissionError:
            raise BackupError(_fda_message(backup_dir))
        except sqlite3.DatabaseError as e:
            # An encrypted backup's Manifest.db is not readable SQLite.
            raise BackupError(
                f"Manifest.db could not be read ({e}).\n"
                "This almost always means the backup is encrypted."
            ) from e

    p = hashed_path(backup_dir, manifest_file_id(WA_DOMAIN, CHATSTORAGE))
    if p.exists():
        return p

    raise BackupError(
        "WhatsApp's message store is not in this backup.\n"
        "That usually means WhatsApp was excluded, or the backup predates the\n"
        "app. Take a fresh unencrypted backup with WhatsApp installed."
    )


def sidecar_paths(backup_dir: Path) -> list[Path]:
    """The -wal and -shm files, if the backup captured them mid-write.

    Without these, recent messages sitting in the write-ahead log are invisible.
    """
    out: list[Path] = []
    manifest = backup_dir / "Manifest.db"
    if not manifest.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
        try:
            for suffix in ("-wal", "-shm"):
                row = con.execute(
                    "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
                    (WA_DOMAIN, CHATSTORAGE + suffix),
                ).fetchone()
                if row:
                    p = hashed_path(backup_dir, row[0])
                    if p.exists():
                        out.append(p)
        finally:
            con.close()
    except (PermissionError, sqlite3.DatabaseError):
        pass
    return out


class StagedDatabase:
    """Copy the store somewhere private and open it there.

    Never opened in place: SQLite may need to replay the write-ahead log, which
    writes, and writing into a backup would corrupt it. The copy lives in a
    0700 temp dir and is deleted on exit.
    """

    def __init__(self, backup_dir: Path):
        self._backup_dir = backup_dir
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        src = find_chatstorage(self._backup_dir)
        self._tmp = tempfile.TemporaryDirectory(prefix="wa-import-")
        tmp = Path(self._tmp.name)
        tmp.chmod(0o700)
        dst = tmp / CHATSTORAGE
        shutil.copy2(src, dst)
        for extra in sidecar_paths(self._backup_dir):
            # Name them so SQLite pairs them with the copied database.
            suffix = "-wal" if extra.name.endswith("wal") or "wal" in extra.name else "-shm"
            try:
                shutil.copy2(extra, tmp / (CHATSTORAGE + suffix))
            except OSError:
                pass
        dst.chmod(0o600)
        self.path = dst
        return dst

    def __exit__(self, *exc) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
        self._tmp = None
        self.path = None
