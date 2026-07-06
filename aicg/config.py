from __future__ import annotations

import os
import tomllib
from pathlib import Path


DEFAULT_CONFIG = {
    "thresholds": {
        "high_session_tokens": 200000,
        "high_output_tokens": 10000,
        "high_output_token_ratio": 0.5,
        "low_cache_hit_ratio": 0.2,
        "low_cache_hit_min_input_tokens": 50000,
        "single_tool_output_bloat_bytes": 102400,
        "session_tool_output_bloat_bytes": 1048576,
        "mcp_overuse_count": 30,
        "retry_loop_turns": 3,
        "background_session_tokens": 50000,
        "project_hotspot_min_sessions": 2,
        "project_hotspot_failure_rate": 0.5,
        "long_running_minutes": 60,
        "large_history_file_bytes": 104857600,
    },
    "prices": {},
}


DEFAULT_CONFIG_TOML = """[thresholds]
high_session_tokens = 200000
high_output_tokens = 10000
high_output_token_ratio = 0.5
low_cache_hit_ratio = 0.2
low_cache_hit_min_input_tokens = 50000
single_tool_output_bloat_bytes = 102400
session_tool_output_bloat_bytes = 1048576
mcp_overuse_count = 30
retry_loop_turns = 3
background_session_tokens = 50000
project_hotspot_min_sessions = 2
project_hotspot_failure_rate = 0.5
long_running_minutes = 60
large_history_file_bytes = 104857600

[prices]
# Model prices are intentionally blank by default.
# Add exact provider:model tables when you want local cost estimates.
#
# [prices."codex:gpt-5.5"]
# input_per_mtok_usd = 0
# cached_input_per_mtok_usd = 0
# output_per_mtok_usd = 0
# reasoning_output_per_mtok_usd = 0
# cache_creation_input_per_mtok_usd = 0
# cache_read_input_per_mtok_usd = 0
# credit_per_usd = 1
"""


def default_app_dir() -> Path:
    return Path(os.environ.get("AICG_HOME", "~/.aicg")).expanduser()


def app_paths(app_dir: Path | None = None) -> dict[str, Path]:
    root = (app_dir or default_app_dir()).expanduser()
    return {
        "app": root,
        "config": root / "config.toml",
        "db": root / "aicg.sqlite",
        "raw": root / "raw",
        "raw_codex": root / "raw" / "codex",
        "raw_claude": root / "raw" / "claude",
        "reports": root / "reports",
    }


def ensure_app_dirs(app_dir: Path | None = None) -> dict[str, str]:
    paths = app_paths(app_dir)
    status: dict[str, str] = {}
    for key in ("app", "raw", "raw_codex", "raw_claude", "reports"):
        path = paths[key]
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        status[str(path)] = "existing" if existed else "created"
    existed = paths["config"].exists()
    if not existed:
        paths["config"].write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    status[str(paths["config"])] = "existing" if existed else "created"
    return status


def load_config(app_dir: Path | None = None) -> dict:
    paths = app_paths(app_dir)
    config = {
        "thresholds": dict(DEFAULT_CONFIG["thresholds"]),
        "prices": dict(DEFAULT_CONFIG["prices"]),
    }
    if paths["config"].exists():
        with paths["config"].open("rb") as handle:
            loaded = tomllib.load(handle)
        for section, values in loaded.items():
            if isinstance(values, dict):
                config.setdefault(section, {})
                config[section].update(values)
    return config
