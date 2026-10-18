"""Tests for locating the message store inside a backup."""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3

import pytest

from whatsapp_import.backup import (
    CHATSTORAGE,
    WA_DOMAIN,
    BackupError,
    describe,
    find_chatstorage,
    hashed_path,
    manifest_file_id,
    require_usable,
)


def test_manifest_file_id_is_sha1_of_domain_dash_path():
    # The whole lookup rests on this one hash, so it is pinned against an
    # independently computed digest rather than the implementation.
    expected = hashlib.sha1(f"{WA_DOMAIN}-{CHATSTORAGE}".encode()).hexdigest()
    assert manifest_file_id(WA_DOMAIN, CHATSTORAGE) == expected
    assert len(expected) == 40


def test_manifest_file_id_known_vector():
    assert manifest_file_id("HomeDomain", "Library/SMS/sms.db") == (
        hashlib.sha1(b"HomeDomain-Library/SMS/sms.db").hexdigest()
    )


def test_hashed_path_uses_the_two_char_shard(tmp_path):
    fid = "abcdef0123456789abcdef0123456789abcdef01"
    assert hashed_path(tmp_path, fid) == tmp_path / "ab" / fid


def _make_backup(tmp_path, *, encrypted=False, with_manifest_db=True, present=True):
    b = tmp_path / "backup"
    b.mkdir()
    with (b / "Manifest.plist").open("wb") as fh:
        plistlib.dump({"IsEncrypted": encrypted}, fh)
    with (b / "Info.plist").open("wb") as fh:
        plistlib.dump({"Device Name": "iPhone", "Last Backup Date": "2026-09-01"}, fh)

    fid = manifest_file_id(WA_DOMAIN, CHATSTORAGE)
    if present:
        target = hashed_path(b, fid)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"SQLite format 3\x00")

    if with_manifest_db:
        con = sqlite3.connect(b / "Manifest.db")
        con.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT)")
        con.execute("INSERT INTO Files VALUES (?,?,?)", (fid, WA_DOMAIN, CHATSTORAGE))
        con.commit()
        con.close()
    return b


def test_finds_store_via_manifest(tmp_path):
    b = _make_backup(tmp_path)
    assert find_chatstorage(b).exists()


def test_falls_back_to_computed_hash_without_manifest(tmp_path):
    # Some backups have an unreadable Manifest.db; the computed hash is the
    # backstop so a readable store is still found.
    b = _make_backup(tmp_path, with_manifest_db=False)
    assert find_chatstorage(b).exists()


def test_missing_store_explains_itself(tmp_path):
    b = _make_backup(tmp_path, with_manifest_db=False, present=False)
    with pytest.raises(BackupError) as e:
        find_chatstorage(b)
    assert "WhatsApp" in str(e.value)


def test_encrypted_backup_is_refused_with_the_actual_fix(tmp_path):
    # An encrypted backup cannot be salvaged by turning the setting off, so
    # the message has to say "take a new one" rather than "check the setting".
    b = _make_backup(tmp_path, encrypted=True)
    info = describe(b)
    assert info.encrypted is True
    with pytest.raises(BackupError) as e:
        require_usable(info)
    msg = str(e.value)
    assert "NEW backup" in msg


def test_unencrypted_backup_passes(tmp_path):
    require_usable(describe(_make_backup(tmp_path)))


def test_describe_reads_device_metadata(tmp_path):
    info = describe(_make_backup(tmp_path))
    assert info.device_name == "iPhone"
    assert info.last_backup == "2026-09-01"
