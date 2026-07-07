from pathlib import Path

from aicg.cli import _read_provider_file
from aicg.db import connect, count_rows, import_records, init_db
from aicg.readers.codex_jsonl import CodexJsonlReader


FIXTURES = Path(__file__).parent / "fixtures"


class ExplodingReader:
    def read(self, path: Path):
        raise RuntimeError("SECRET_TOKEN=should-not-be-stored")


def test_scan_file_error_is_isolated_and_deduplicated(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    bad_path = tmp_path / "bad.jsonl"
    bad_path.write_text("bad", encoding="utf-8")
    init_db(db_path)
    records = CodexJsonlReader().read(FIXTURES / "codex_exec_sample.jsonl")

    with connect(db_path) as conn:
        import_records(conn, records)
        assert _read_provider_file(conn, "codex", ExplodingReader(), bad_path) is None
        assert _read_provider_file(conn, "codex", ExplodingReader(), bad_path) is None
        conn.commit()

        row = conn.execute(
            "SELECT error_type, error_message_hash FROM scan_errors"
        ).fetchone()
        columns = {item["name"] for item in conn.execute("PRAGMA table_info(scan_errors)")}

        assert count_rows(conn, "sessions") == 1
        assert count_rows(conn, "scan_errors") == 1
        assert row["error_type"] == "RuntimeError"
        assert row["error_message_hash"] != "SECRET_TOKEN=should-not-be-stored"
        assert "error_message" not in columns


def test_successful_rescan_clears_prior_scan_error(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    path = tmp_path / "recover.jsonl"
    path.write_text("bad", encoding="utf-8")
    init_db(db_path)

    with connect(db_path) as conn:
        assert _read_provider_file(conn, "codex", ExplodingReader(), path) is None
        assert count_rows(conn, "scan_errors") == 1
        path.write_text(
            '{"type":"session_meta","timestamp":"2026-07-05T00:00:00Z","payload":{"id":"recovered"}}\n',
            encoding="utf-8",
        )
        records = _read_provider_file(conn, "codex", CodexJsonlReader(), path)
        assert records is not None
        conn.commit()

        assert count_rows(conn, "scan_errors") == 0
