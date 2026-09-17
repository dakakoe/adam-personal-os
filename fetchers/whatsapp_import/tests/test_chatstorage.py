"""Parser tests, including an end-to-end run against a synthetic
ChatStorage.sqlite built to match the real Core Data shape.

Building a fake store rather than mocking is deliberate: the risk in this
importer is SQL and schema assumptions, and a mock would agree with whatever
those assumptions happened to be.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from whatsapp_import.chatstorage import (
    CORE_DATA_EPOCH,
    chat_key_for,
    contacts_from,
    coredata_to_dt,
    groups_from,
    inspect,
    is_group,
    iter_messages,
    kind_for,
    normalize_jid,
    to_e164,
)

SELF = "15551234567@s.whatsapp.net"
ALICE = "3725551234@s.whatsapp.net"
GROUP = "120363012345678901@g.us"
LIDCHAT = "987654321098765@lid"


def _secs(dt: datetime) -> float:
    return (dt - CORE_DATA_EPOCH).total_seconds()


@pytest.fixture
def store(tmp_path):
    """A minimal but realistically-shaped ChatStorage.sqlite."""
    p = tmp_path / "ChatStorage.sqlite"
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE ZWACHATSESSION (
            Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
            ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER, ZLASTMESSAGEDATE REAL);
        CREATE TABLE ZWAGROUPMEMBER (
            Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT,
            ZCONTACTNAME TEXT, ZCHATSESSION INTEGER);
        CREATE TABLE ZWAMEDIAITEM (
            Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER,
            ZMEDIALOCALPATH TEXT, ZVCARDSTRING TEXT, ZTITLE TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (
            Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
        CREATE TABLE ZWAMESSAGE (
            Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZSTANZAID TEXT,
            ZFROMJID TEXT, ZTOJID TEXT, ZISFROMME INTEGER,
            ZMESSAGEDATE REAL, ZSENTDATE REAL, ZTEXT TEXT,
            ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER);
        """
    )
    t = _secs(datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))
    con.executemany(
        "INSERT INTO ZWACHATSESSION (Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE) VALUES (?,?,?,?)",
        [(1, ALICE, "Alice Realname", 0),
         (2, GROUP, "Founders", 1),
         (3, LIDCHAT, None, 0),
         (4, "status@broadcast", "Status", 0)],
    )
    con.execute(
        "INSERT INTO ZWAGROUPMEMBER (Z_PK, ZMEMBERJID, ZCONTACTNAME, ZCHATSESSION) VALUES (?,?,?,?)",
        (10, ALICE, "Alice", 2),
    )
    con.execute(
        "INSERT INTO ZWAPROFILEPUSHNAME (Z_PK, ZJID, ZPUSHNAME) VALUES (?,?,?)",
        (1, ALICE, "ali 🚀"),
    )
    con.executemany(
        """INSERT INTO ZWAMESSAGE
           (Z_PK, ZCHATSESSION, ZSTANZAID, ZFROMJID, ZTOJID, ZISFROMME,
            ZMESSAGEDATE, ZSENTDATE, ZTEXT, ZMESSAGETYPE, ZGROUPMEMBER)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            # inbound text from Alice
            (1, 1, "AAA1", ALICE, SELF, 0, t, t, "hello there", 0, None),
            # outbound text to Alice
            (2, 1, "AAA2", SELF, ALICE, 1, t + 60, t + 60, "hi back", 0, None),
            # group message from Alice
            (3, 2, "BBB1", ALICE, GROUP, 0, t + 120, t + 120, "in the group", 0, 10),
            # voice note (media item added below)
            (4, 1, "AAA3", ALICE, SELF, 0, t + 180, t + 180, None, 3, None),
            # a status post — must be dropped
            (5, 4, "CCC1", ALICE, SELF, 0, t + 240, t + 240, "status", 0, None),
            # group system event: no stanza id — must be dropped
            (6, 2, None, ALICE, GROUP, 0, t + 300, t + 300, None, 0, 10),
            # broken timestamp, sent_date rescues it
            (7, 1, "AAA4", ALICE, SELF, 0, 0, t + 360, "rescued", 0, None),
            # both timestamps unusable — must be dropped
            (8, 1, "AAA5", ALICE, SELF, 0, 0, 0, "no date", 0, None),
            # message in the lid-only chat
            (9, 3, "DDD1", None, LIDCHAT, 1, t + 420, t + 420, "to hidden", 0, None),
        ],
    )
    con.execute(
        "INSERT INTO ZWAMEDIAITEM (Z_PK, ZMESSAGE, ZMEDIALOCALPATH, ZVCARDSTRING) VALUES (?,?,?,?)",
        (1, 4, "Media/x/1/2/AUDIO.opus", "audio/ogg; codecs=opus"),
    )
    con.commit()
    con.close()
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


class TestTimestamps:
    def test_epoch_is_2001_not_1970(self):
        assert coredata_to_dt(0) is None  # zero means "unset", not the epoch
        # Same number of seconds read against the two epochs is 31 years
        # apart. Reading a 2026 value as Unix time would land in 1995.
        secs = _secs(datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert coredata_to_dt(secs).year == 2026
        assert datetime.fromtimestamp(secs, timezone.utc).year == 1995

    def test_known_value_round_trips(self):
        want = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        assert coredata_to_dt(_secs(want)) == want

    def test_unix_timestamp_is_rejected_not_silently_wrong(self):
        # A Unix timestamp read as Core Data seconds lands ~31 years late.
        # Wrong dates poison follow-up scheduling, so they must be dropped.
        assert coredata_to_dt(1_700_000_000) is None

    @pytest.mark.parametrize("bad", [None, -1, "abc", float("nan"), 10**12])
    def test_junk(self, bad):
        assert coredata_to_dt(bad) is None


class TestJid:
    def test_device_suffix_stripped(self):
        assert normalize_jid("3725551234:12@s.whatsapp.net") == ALICE

    def test_chat_keys_match_the_bridge(self):
        # These MUST agree with jid.js or a person imports twice.
        assert chat_key_for(ALICE) == "3725551234"
        assert chat_key_for(GROUP) == GROUP
        assert chat_key_for(LIDCHAT) == "lid:987654321098765"
        assert chat_key_for("status@broadcast") is None

    def test_lid_is_not_a_phone_number(self):
        assert to_e164(LIDCHAT) is None
        assert is_group(GROUP) and not is_group(ALICE)


class TestKind:
    def test_voice_note_distinguished_from_audio(self):
        # Only voice notes get transcribed.
        assert kind_for(3, "audio/ogg; codecs=opus", True) == "voice"
        assert kind_for(3, "audio/mpeg", True) == "audio"

    def test_mime_beats_the_type_number(self):
        assert kind_for(0, "image/jpeg", True) == "image"
        assert kind_for(1, "video/mp4", True) == "video"

    def test_webp_is_a_sticker(self):
        assert kind_for(1, "image/webp", True) == "sticker"

    def test_media_type_without_media_is_not_media(self):
        assert kind_for(1, None, False) == "other"

    def test_unknown_type_degrades(self):
        assert kind_for(99, None, False) == "other"
        assert kind_for(None, None, False) == "other"


class TestIterMessages:
    def test_extracts_the_real_messages_only(self, store):
        msgs = list(iter_messages(store, self_jid=SELF))
        ids = [m["source_message_id"] for m in msgs]
        # Dropped: the status post, the id-less system event, the dateless row.
        assert ids == ["AAA1", "AAA2", "BBB1", "AAA3", "AAA4", "DDD1"]

    def test_direction_and_counterparty(self, store):
        by_id = {m["source_message_id"]: m for m in iter_messages(store, self_jid=SELF)}
        assert by_id["AAA1"]["from_me"] is False
        assert by_id["AAA1"]["sender_jid"] == ALICE
        assert by_id["AAA2"]["from_me"] is True
        assert by_id["AAA2"]["sender_jid"] == SELF
        # An outbound DM must still resolve to the counterparty's chat, or it
        # would import with no person — the bug the live bridge had.
        assert by_id["AAA2"]["chat_key"] == "3725551234"

    def test_group_sender_resolves_through_the_member_table(self, store):
        m = next(x for x in iter_messages(store, self_jid=SELF) if x["source_message_id"] == "BBB1")
        assert m["is_group"] is True
        assert m["sender_jid"] == ALICE
        assert m["chat_title"] == "Founders"

    def test_voice_note_detected_from_mime(self, store):
        m = next(x for x in iter_messages(store, self_jid=SELF) if x["source_message_id"] == "AAA3")
        assert m["kind"] == "voice"
        assert m["payload"]["media_local_path"].endswith(".opus")

    def test_sent_date_rescues_a_zero_message_date(self, store):
        m = next(x for x in iter_messages(store, self_jid=SELF) if x["source_message_id"] == "AAA4")
        assert m["message_date"].startswith("2026-05-01")

    def test_lid_chat_keeps_its_namespace(self, store):
        m = next(x for x in iter_messages(store, self_jid=SELF) if x["source_message_id"] == "DDD1")
        assert m["chat_key"] == "lid:987654321098765"
        assert m["sender_phone_e164"] == "15551234567"

    def test_only_chat_filter(self, store):
        msgs = list(iter_messages(store, self_jid=SELF, only_chat=GROUP))
        assert [m["source_message_id"] for m in msgs] == ["BBB1"]

    def test_payload_carries_no_blobs(self, store):
        # 200k messages times a Core Data blob is hundreds of MB of JSONB.
        for m in iter_messages(store, self_jid=SELF):
            assert set(m["payload"]) <= {
                "origin", "z_pk", "message_type", "mime", "media_local_path"
            }


class TestContactsAndGroups:
    def test_address_book_name_is_captured(self, store):
        contacts = {c["jid"]: c for c in contacts_from(store, self_jid=SELF)}
        # This is the whole point of the import: the real name, which the live
        # bridge can never see.
        assert contacts[ALICE]["notify_name"] == "Alice Realname"
        assert contacts[ALICE]["push_name"] == "ali 🚀"
        assert contacts[ALICE]["phone_e164"] == "3725551234"

    def test_groups_and_broadcasts_are_not_contacts(self, store):
        jids = {c["jid"] for c in contacts_from(store, self_jid=SELF)}
        assert GROUP not in jids
        assert "status@broadcast" not in jids

    def test_lid_contact_has_no_phone(self, store):
        contacts = {c["jid"]: c for c in contacts_from(store, self_jid=SELF)}
        assert contacts[LIDCHAT]["phone_e164"] is None
        assert contacts[LIDCHAT]["lid"] == LIDCHAT

    def test_groups_listed_with_titles(self, store):
        groups = list(groups_from(store))
        assert groups == [{"chat_jid": GROUP, "title": "Founders"}]


class TestSchemaDrift:
    def test_missing_optional_table_is_survivable(self, tmp_path):
        # An older or newer WhatsApp may not have every table. Losing push
        # names must not lose the import.
        p = tmp_path / "s.sqlite"
        con = sqlite3.connect(p)
        con.executescript(
            """
            CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
            CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                ZSTANZAID TEXT, ZISFROMME INTEGER, ZMESSAGEDATE REAL, ZTEXT TEXT);
            """
        )
        con.execute("INSERT INTO ZWACHATSESSION VALUES (1, ?, 'Alice')", (ALICE,))
        con.execute(
            "INSERT INTO ZWAMESSAGE VALUES (1, 1, 'X1', 0, ?, 'hi')",
            (_secs(datetime(2026, 5, 1, tzinfo=timezone.utc)),),
        )
        con.commit()
        con.close()
        ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        msgs = list(iter_messages(ro))
        assert len(msgs) == 1
        assert msgs[0]["text"] == "hi"

    def test_not_a_whatsapp_store_says_so(self, tmp_path):
        p = tmp_path / "other.sqlite"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE unrelated (x INTEGER)")
        con.commit()
        con.close()
        ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        with pytest.raises(LookupError):
            list(iter_messages(ro))

    def test_inspect_reports_shape(self, store):
        rep = inspect(store)
        assert "ZWAMESSAGE" in rep["columns"]
        assert rep["counts"]["ZWAMESSAGE"] == 9
        assert rep["message_type_histogram"]
