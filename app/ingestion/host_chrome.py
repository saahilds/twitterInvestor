"""Start and stop the Windows Chrome that WSL attaches to over CDP."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.config.settings import Settings

logger = logging.getLogger("trading_bot")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def to_windows_path(path: Path) -> str:
    """Map a WSL /mnt/<drive>/... path to a Windows path. Other paths pass through."""
    text = path.resolve().as_posix()
    marker = "/mnt/"
    if text.startswith(marker) and len(text) > len(marker) + 1 and text[len(marker) + 1] == "/":
        drive = text[len(marker)].upper()
        rest = text[len(marker) + 2 :].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return str(path.resolve())


def powershell_executable() -> str | None:
    """WSL systemd does not put Windows on PATH, so look up powershell.exe directly."""
    found = shutil.which("powershell.exe")
    if found:
        return found
    for candidate in (
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Windows/Sysnative/WindowsPowerShell/v1.0/powershell.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _run_windows_script(script_name: str) -> bool:
    powershell = powershell_executable()
    if powershell is None:
        logger.info(
            "host_chrome_skipped",
            extra={"event_type": "x_profile", "script": script_name, "reason": "powershell_not_found"},
        )
        return False
    script = repo_root() / "scripts" / "runner" / script_name
    if not script.is_file():
        logger.warning(
            "host_chrome_script_missing",
            extra={"event_type": "x_profile", "script": script_name},
        )
        return False
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            to_windows_path(script),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.warning(
            "host_chrome_script_failed",
            extra={
                "event_type": "x_profile",
                "script": script_name,
                "returncode": completed.returncode,
                "detail": detail[-500:],
            },
        )
        return False
    return True


def start_bot_chrome(settings: Settings) -> bool:
    """Launch the Windows bot Chrome if this process is in WSL and CDP is configured."""
    if not (settings.playwright_cdp_url or "").strip():
        return False
    return _run_windows_script("start_chrome.ps1")


def quit_bot_chrome(settings: Settings) -> bool:
    """Gracefully quit the Windows bot Chrome so its profile, including the X login, is saved."""
    if not (settings.playwright_cdp_url or "").strip():
        return False
    return _run_windows_script("stop_chrome.ps1")
