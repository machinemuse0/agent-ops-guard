from pathlib import Path
import os

from aicg.db import connect, count_rows, import_records, init_db
from aicg.cli import _source_needs_scan
from aicg.db import upsert_scan_state
from aicg.readers.codex_jsonl import CodexJsonlReader
from aicg.util import hash_file


FIXTURES = Path(__file__).parent / "fixtures"


def test_importing_same_scan_twice_is_idempotent(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    records = CodexJsonlReader().read(FIXTURES / "codex_exec_sample.jsonl")

    with connect(db_path) as conn:
        import_records(conn, records)
        import_records(conn, records)
        conn.commit()

        assert count_rows(conn, "sessions") == 1
        assert count_rows(conn, "turns") == 1
        assert count_rows(conn, "tool_events") == 1


def test_scan_state_detects_same_size_same_mtime_hash_change(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    source = tmp_path / "source.jsonl"
    source.write_text('{"a":1}\n', encoding="utf-8")
    init_db(db_path)
    stat = source.stat()

    with connect(db_path) as conn:
        upsert_scan_state(
            conn,
            provider="codex",
            source_file=str(source),
            file_hash=hash_file(source),
            file_size=stat.st_size,
            mtime=stat.st_mtime,
            last_line=1,
        )
        conn.commit()

        source.write_text('{"b":2}\n', encoding="utf-8")
        os.utime(source, (stat.st_atime, stat.st_mtime))

        assert _source_needs_scan(conn, source) is True
