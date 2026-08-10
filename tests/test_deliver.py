from __future__ import annotations

import sqlite3

import pytest

from picatchup import deliver, users
from picatchup.config import Settings
from picatchup.errors import PiCatchUpError, ProviderError
from picatchup.providers.telegram import split_message
from picatchup.util import new_id, utc_now


def add_recap(conn: sqlite3.Connection, date: str = "2026-09-15") -> str:
    recap_id = new_id()
    conn.execute(
        "INSERT INTO recaps (id, recap_date, text, model, created_at) VALUES (?, ?, ?, ?, ?)",
        (recap_id, date, "📚 MATEMATICA\n• le derivate", "test-text-model", utc_now()),
    )
    conn.commit()
    return recap_id


class Postbox:
    """A stand-in for Telegram that remembers what it was asked to send."""

    def __init__(self, refuse: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.refuse = refuse or set()

    def __call__(self, text: str, chat_id: int) -> list[int]:
        if chat_id in self.refuse:
            raise ProviderError("bot was blocked by the user")
        self.sent.append((chat_id, text))
        return [len(self.sent)]


class TestSendRecap:
    def test_sends_to_everyone_active(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_recap(conn)
        users.add(conn, 111, "Ada")
        users.add(conn, 222, "Bruno")
        postbox = Postbox()

        result = deliver.send_recap(conn, settings, "2026-09-15", sender=postbox)

        assert sorted(result.sent_to) == ["Ada", "Bruno"]
        assert [chat_id for chat_id, _ in postbox.sent] == [111, 222]
        assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 2

    def test_nobody_gets_the_same_recap_twice(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_recap(conn)
        users.add(conn, 111, "Ada")
        postbox = Postbox()

        deliver.send_recap(conn, settings, "2026-09-15", sender=postbox)
        again = deliver.send_recap(conn, settings, "2026-09-15", sender=postbox)

        assert len(postbox.sent) == 1
        assert again.already_had_it == ["Ada"] and again.sent_to == []

    def test_force_sends_it_again(self, conn: sqlite3.Connection, settings: Settings) -> None:
        add_recap(conn)
        users.add(conn, 111, "Ada")
        postbox = Postbox()

        deliver.send_recap(conn, settings, "2026-09-15", sender=postbox)
        deliver.send_recap(conn, settings, "2026-09-15", sender=postbox, force=True)

        assert len(postbox.sent) == 2
        assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1

    def test_one_blocked_bot_does_not_stop_the_class(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_recap(conn)
        users.add(conn, 111, "Ada")
        users.add(conn, 222, "Bruno")
        postbox = Postbox(refuse={111})

        result = deliver.send_recap(conn, settings, "2026-09-15", sender=postbox)

        assert result.failed == ["Ada"] and result.sent_to == ["Bruno"]
        assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1

    def test_a_failure_is_retried_next_time(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        """No delivery row means it never arrived, so the next run tries again."""
        add_recap(conn)
        users.add(conn, 111, "Ada")
        deliver.send_recap(conn, settings, "2026-09-15", sender=Postbox(refuse={111}))

        working = Postbox()
        result = deliver.send_recap(conn, settings, "2026-09-15", sender=working)

        assert result.sent_to == ["Ada"] and len(working.sent) == 1

    def test_a_removed_person_receives_nothing(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_recap(conn)
        users.add(conn, 111, "Ada")
        users.remove(conn, 111)

        with pytest.raises(PiCatchUpError, match="nobody to send it to"):
            deliver.send_recap(conn, settings, "2026-09-15", sender=Postbox())

    def test_a_missing_recap_says_which_command_to_run(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        users.add(conn, 111, "Ada")

        with pytest.raises(PiCatchUpError, match="picatchup recap 2026-09-15"):
            deliver.send_recap(conn, settings, "2026-09-15", sender=Postbox())


class TestSplitMessage:
    def test_a_short_recap_is_one_message(self) -> None:
        assert split_message("ciao") == ["ciao"]

    def test_a_long_one_breaks_between_paragraphs(self) -> None:
        text = "a" * 60 + "\n\n" + "b" * 60

        assert split_message(text, limit=100) == ["a" * 60, "b" * 60]

    def test_it_never_exceeds_the_limit_even_without_spaces(self) -> None:
        parts = split_message("x" * 250, limit=100)

        assert all(len(part) <= 100 for part in parts)
        assert "".join(parts) == "x" * 250

    def test_nothing_to_send_is_no_messages(self) -> None:
        assert split_message("   \n ") == []
