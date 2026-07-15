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
        "format_drift_unknown_event_ratio": 0.05,
    },
    "prices": {},
    "review": {
        "repeated_error_hash_min": 3,
        "edit_failure_run_min": 3,
        "context_growth_min_turns": 5,
        "context_growth_multiple": 3.0,
        "context_cache_drop_min": 0.15,
        "tool_output_spike_min_bytes": 102400,
        "tool_output_spike_multiplier": 5.0,
        "tool_output_failure_window_turns": 2,
        "early_shell_window": 5,
        "early_shell_failure_score": 2.0,
        "no_progress_min_turns": 20,
        "no_progress_edit_success_max": 0.3,
        "model_fallback_failure_multiplier": 2.0,
    },
    "alerts": {},
    "budget": {"windows": []},
    "team": {"member_label": "", "projects": []},
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
format_drift_unknown_event_ratio = 0.05

[prices]
# Model prices are intentionally blank by default.
# Add exact provider:model tables when you want local cost estimates.
#
# [prices."codex:gpt-5.5"]
# input_per_mtok_usd = 0
# cached_input_per_mtok_usd = 0
# output_per_mtok_usd = 0
# cache_creation_input_per_mtok_usd = 0
# cache_read_input_per_mtok_usd = 0
# credit_per_usd = 1

[review]
repeated_error_hash_min = 3
edit_failure_run_min = 3
context_growth_min_turns = 5
context_growth_multiple = 3.0
context_cache_drop_min = 0.15
tool_output_spike_min_bytes = 102400
tool_output_spike_multiplier = 5.0
tool_output_failure_window_turns = 2
early_shell_window = 5
early_shell_failure_score = 2.0
no_progress_min_turns = 20
no_progress_edit_success_max = 0.3
model_fallback_failure_multiplier = 2.0

# [alerts]
# daily_cost_usd_max = 5.0
# daily_waste_rate_max = 0.3
# new_policy_violations_max = 0
# interrupted_sessions_max = 3

# [[budget.windows]]
# name = "codex-5h"
# provider = "codex"
# window = "5h"
# max_tokens = 1000000
# max_cost_usd = 10.0

# [team]
# member_label = "developer-1"
#
# [[team.projects]]
# alias = "project-a"
# path = "/absolute/local/path"
"""


DEFAULT_POLICY_TOML = """[policy]
version = 1

[policy.secrets]
enabled = true
builtin = ["openai_key", "anthropic_key", "github_token", "bearer_header", "generic_env_assignment"]

[policy.sensitive_paths]
enabled = true
builtin = ["dot_env", "ssh_keys", "cloud_credentials"]

[policy.services]
mode = "denylist"
deny = ["api.openai.com"]
allow = []
flag_mentions = false

[policy.raw_payload]
threshold_bytes = 65536
"""


def default_app_dir() -> Path:
    return Path(os.environ.get("AICG_HOME", "~/.aicg")).expanduser()


def app_paths(app_dir: Path | None = None) -> dict[str, Path]:
    root = (app_dir or default_app_dir()).expanduser()
    return {
        "app": root,
        "config": root / "config.toml",
        "policy": root / "policy.toml",
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
    policy_existed = paths["policy"].exists()
    if not policy_existed:
        paths["policy"].write_text(DEFAULT_POLICY_TOML, encoding="utf-8")
    status[str(paths["policy"])] = "existing" if policy_existed else "created"
    return status


def load_config(app_dir: Path | None = None) -> dict:
    paths = app_paths(app_dir)
    config = {
        "thresholds": dict(DEFAULT_CONFIG["thresholds"]),
        "prices": dict(DEFAULT_CONFIG["prices"]),
        "review": dict(DEFAULT_CONFIG["review"]),
        "alerts": dict(DEFAULT_CONFIG["alerts"]),
        "budget": {"windows": list(DEFAULT_CONFIG["budget"]["windows"])},
        "team": {
            "member_label": DEFAULT_CONFIG["team"]["member_label"],
            "projects": list(DEFAULT_CONFIG["team"]["projects"]),
        },
    }
    if paths["config"].exists():
        with paths["config"].open("rb") as handle:
            loaded = tomllib.load(handle)
        for section, values in loaded.items():
            if isinstance(values, dict):
                config.setdefault(section, {})
                config[section].update(values)
    return config
