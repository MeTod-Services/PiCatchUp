-- The whole database. Six tables, no migrations.
--
-- Every statement is "IF NOT EXISTS", so running `picatchup init` twice is
-- harmless. While the schema is still changing, the way to change it is to
-- delete data/picatchup.db and run init again — there is nothing in there yet
-- that you cannot regenerate.
--
-- Ids are text (uuid hex). Timestamps are UTC ISO-8601 text.
-- Dates are "YYYY-MM-DD" in the local timezone from .env.


-- One row per recording. One recording is one lesson.
CREATE TABLE IF NOT EXISTS lessons (
    id            TEXT PRIMARY KEY,
    lesson_date   TEXT    NOT NULL,          -- YYYY-MM-DD
    subject       TEXT,                      -- NULL until we know it
    audio_path    TEXT,                      -- NULL once the audio is purged
    audio_sha256  TEXT    NOT NULL UNIQUE,   -- same file = same number, so a
                                             -- file is never transcribed twice
    duration_ms   INTEGER,
    text          TEXT,                      -- NULL until transcribed
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS lessons_by_date ON lessons (lesson_date);


-- A lesson cut into searchable pieces, each one knowing where in the
-- recording it came from. That is what lets an answer cite a minute.
CREATE TABLE IF NOT EXISTS chunks (
    id         TEXT    PRIMARY KEY,
    lesson_id  TEXT    NOT NULL REFERENCES lessons (id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,             -- 0, 1, 2... within the lesson
    start_ms   INTEGER NOT NULL,
    end_ms     INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    UNIQUE (lesson_id, idx)
);


-- The search index (SQLite's built-in full-text search).
--
-- This keeps its own copy of the text, which costs a little disk and saves you
-- the three triggers that an "external content" FTS5 table would need. Write to
-- it in the same place you insert a chunk.
--
-- remove_diacritics 2 is what makes searching "perche" find "perché".
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    chunk_id UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);


-- One summary per school day, shared by the whole class.
CREATE TABLE IF NOT EXISTS recaps (
    id          TEXT PRIMARY KEY,
    recap_date  TEXT NOT NULL UNIQUE,
    text        TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);


-- Who is allowed to use the bot. No row here means no access at all.
CREATE TABLE IF NOT EXISTS users (
    id                TEXT    PRIMARY KEY,
    telegram_user_id  INTEGER NOT NULL UNIQUE,
    display_name      TEXT    NOT NULL,
    is_admin          INTEGER NOT NULL DEFAULT 0,
    is_active         INTEGER NOT NULL DEFAULT 1,
    is_muted          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL
);


-- Proof that a recap was sent. The primary key is the pair, so the database
-- itself refuses to send the same recap to the same person twice — no code
-- needed.
CREATE TABLE IF NOT EXISTS deliveries (
    recap_id             TEXT    NOT NULL REFERENCES recaps (id) ON DELETE CASCADE,
    user_id              TEXT    NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    telegram_message_id  INTEGER,
    sent_at              TEXT    NOT NULL,
    PRIMARY KEY (recap_id, user_id)
);
