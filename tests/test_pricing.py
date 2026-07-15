from aicg.db import connect, init_db, upsert_session, upsert_turn
from aicg.models import NormalizedSession, NormalizedTurn
from aicg.pricing import apply_pricing
from aicg.util import utc_now_iso


def test_pricing_keeps_cost_null_without_config(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        _insert_pricing_fixture(conn, now, model="gpt-5.5")
        result = apply_pricing(conn, ["session-priced"], {"prices": {}})
        turn = conn.execute("SELECT estimated_cost_usd, credit_estimate FROM turns").fetchone()
        session = conn.execute("SELECT estimated_cost_usd, credit_estimate FROM sessions").fetchone()

    assert result == {"turns_priced": 0, "sessions_priced": 0}
    assert turn["estimated_cost_usd"] is None
    assert turn["credit_estimate"] is None
    assert session["estimated_cost_usd"] is None
    assert session["credit_estimate"] is None


def test_pricing_applies_exact_provider_model_match(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()
    config = {
        "prices": {
            "codex:gpt-5.5": {
                "input_per_mtok_usd": 1,
                "cached_input_per_mtok_usd": 0,
                "output_per_mtok_usd": 2,
                "cache_creation_input_per_mtok_usd": 0,
                "cache_read_input_per_mtok_usd": 0,
                "credit_per_usd": 3,
            }
        }
    }

    with connect(db_path) as conn:
        _insert_pricing_fixture(conn, now, model="gpt-5.5")
        result = apply_pricing(conn, ["session-priced"], config)
        turn = conn.execute("SELECT estimated_cost_usd, credit_estimate FROM turns").fetchone()
        session = conn.execute("SELECT estimated_cost_usd, credit_estimate FROM sessions").fetchone()

    assert result == {"turns_priced": 1, "sessions_priced": 1}
    assert turn["estimated_cost_usd"] == 2.0
    assert turn["credit_estimate"] == 6.0
    assert session["estimated_cost_usd"] == 2.0
    assert session["credit_estimate"] == 6.0


def test_codex_pricing_does_not_double_count_cached_or_reasoning_tokens(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()
    config = {
        "prices": {
            "codex:gpt-5.5": {
                "input_per_mtok_usd": 1,
                "cached_input_per_mtok_usd": 0.1,
                "output_per_mtok_usd": 2,
                "credit_per_usd": 1,
            }
        }
    }

    with connect(db_path) as conn:
        upsert_session(
            conn,
            NormalizedSession(
                id="session-codex-overlap",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5.5",
                created_at=now,
            ),
        )
        upsert_turn(
            conn,
            NormalizedTurn(
                id="turn-codex-overlap",
                session_id="session-codex-overlap",
                provider="codex",
                started_at=now,
                status="completed",
                model="gpt-5.5",
                input_tokens=1_000_000,
                cached_input_tokens=250_000,
                output_tokens=500_000,
                reasoning_output_tokens=100_000,
            ),
        )
        apply_pricing(conn, ["session-codex-overlap"], config)
        turn = conn.execute("SELECT estimated_cost_usd FROM turns").fetchone()

    assert turn["estimated_cost_usd"] == 1.775


def test_pricing_does_not_fallback_to_provider_only(tmp_path):
    db_path = tmp_path / "aicg.sqlite"
    init_db(db_path)
    now = utc_now_iso()

    with connect(db_path) as conn:
        _insert_pricing_fixture(conn, now, model="gpt-5.5")
        apply_pricing(
            conn,
            ["session-priced"],
            {"prices": {"codex:gpt-5": {"input_per_mtok_usd": 100}}},
        )
        turn = conn.execute("SELECT estimated_cost_usd, credit_estimate FROM turns").fetchone()

    assert turn["estimated_cost_usd"] is None
    assert turn["credit_estimate"] is None


def _insert_pricing_fixture(conn, now: str, *, model: str) -> None:
    upsert_session(
        conn,
        NormalizedSession(
            id="session-priced",
            provider="codex",
            started_at=now,
            status="completed",
            model=model,
            created_at=now,
        ),
    )
    upsert_turn(
        conn,
        NormalizedTurn(
            id="turn-priced",
            session_id="session-priced",
            provider="codex",
            started_at=now,
            status="completed",
            model=model,
            input_tokens=1_000_000,
            output_tokens=500_000,
        ),
    )
    conn.commit()
