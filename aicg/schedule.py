from __future__ import annotations

import os
import shlex
import sys


SCHEDULE_COMMANDS = {
    "scan": ["scan", "--since", "24h", "--provider", "all"],
    "summary": ["summary", "--period", "day", "--compare", "--format", "md", "--out", "~/.aicg/reports/daily.md"],
    "dashboard": ["dashboard", "--period", "day", "--out", "~/.aicg/reports/dashboard.html"],
}


def render_schedule_text(
    *,
    scheduler: str,
    time_value: str,
    commands_value: str,
    python_executable: str | None = None,
    aicg_home: str | None = None,
) -> str:
    hour, minute = _parse_time(time_value)
    command_names = _parse_commands(commands_value)
    python = python_executable or sys.executable
    home = aicg_home if aicg_home is not None else os.environ.get("AICG_HOME")
    if scheduler == "cron":
        return _cron_text(hour, minute, command_names, python, home)
    if scheduler == "launchd":
        return _launchd_text(hour, minute, command_names, python, home)
    if scheduler == "systemd":
        return _systemd_text(hour, minute, command_names, python, home)
    raise ValueError(f"Unsupported scheduler: {scheduler}")


def _cron_text(hour: int, minute: int, command_names: list[str], python: str, aicg_home: str | None) -> str:
    command = _shell_chain(command_names, python, aicg_home)
    return f"{minute} {hour} * * * {command}\n"


def _launchd_text(hour: int, minute: int, command_names: list[str], python: str, aicg_home: str | None) -> str:
    command = _shell_chain(command_names, python, aicg_home, include_env_prefix=False)
    env_block = ""
    if aicg_home:
        env_block = (
            "  <key>EnvironmentVariables</key>\n"
            "  <dict>\n"
            "    <key>AICG_HOME</key>\n"
            f"    <string>{_xml(aicg_home)}</string>\n"
            "  </dict>\n"
        )
    return "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<plist version=\"1.0\">",
            "<dict>",
            "  <key>Label</key>",
            "  <string>local.aicg.daily</string>",
            env_block.rstrip(),
            "  <key>ProgramArguments</key>",
            "  <array>",
            "    <string>/bin/sh</string>",
            "    <string>-lc</string>",
            f"    <string>{_xml(command)}</string>",
            "  </array>",
            "  <key>StartCalendarInterval</key>",
            "  <dict>",
            "    <key>Hour</key>",
            f"    <integer>{hour}</integer>",
            "    <key>Minute</key>",
            f"    <integer>{minute}</integer>",
            "  </dict>",
            "</dict>",
            "</plist>",
            "",
        ]
    )


def _systemd_text(hour: int, minute: int, command_names: list[str], python: str, aicg_home: str | None) -> str:
    command = _shell_chain(command_names, python, aicg_home, include_env_prefix=False)
    env_line = f"Environment=AICG_HOME={_systemd_escape(aicg_home)}\n" if aicg_home else ""
    return "\n".join(
        [
            "# ~/.config/systemd/user/aicg-daily.service",
            "[Unit]",
            "Description=AgentOps Guard daily local scan",
            "",
            "[Service]",
            "Type=oneshot",
            env_line.rstrip(),
            f"ExecStart=/bin/sh -lc {_systemd_escape(command)}",
            "",
            "# ~/.config/systemd/user/aicg-daily.timer",
            "[Unit]",
            "Description=Run AgentOps Guard daily",
            "",
            "[Timer]",
            f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _shell_chain(
    command_names: list[str],
    python: str,
    aicg_home: str | None,
    *,
    include_env_prefix: bool = True,
) -> str:
    commands = []
    for name in command_names:
        argv = [python, "-m", "aicg", *SCHEDULE_COMMANDS[name]]
        rendered = " ".join(shlex.quote(part) for part in argv)
        if include_env_prefix and aicg_home:
            rendered = f"AICG_HOME={shlex.quote(aicg_home)} {rendered}"
        commands.append(rendered)
    return " && ".join(commands)


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("--time must use HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("--time must use HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("--time must use HH:MM in 00:00-23:59")
    return hour, minute


def _parse_commands(value: str) -> list[str]:
    commands = [item.strip() for item in value.split(",") if item.strip()]
    if not commands:
        raise ValueError("--commands must include at least one command")
    unknown = sorted(set(commands) - set(SCHEDULE_COMMANDS))
    if unknown:
        raise ValueError(f"Unsupported schedule command(s): {', '.join(unknown)}")
    return commands


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _systemd_escape(value: str) -> str:
    return shlex.quote(value)
