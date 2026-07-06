from pathlib import Path

from aicg.db import connect, count_rows, import_records, init_db
from aicg.readers.codex_jsonl import CodexJsonlReader


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
