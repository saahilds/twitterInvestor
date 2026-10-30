from pathlib import Path

from app.ingestion.host_chrome import powershell_executable, to_windows_path


def test_wsl_mount_becomes_windows_path(monkeypatch) -> None:
    def fake_resolve(self: Path) -> Path:
        return Path("/mnt/c/Users/saahi/Documents/GitHub/twitterInvestor/scripts/runner/stop_chrome.ps1")

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    assert (
        to_windows_path(Path("stop_chrome.ps1"))
        == r"C:\Users\saahi\Documents\GitHub\twitterInvestor\scripts\runner\stop_chrome.ps1"
    )


def test_powershell_falls_back_to_windows_system32(monkeypatch) -> None:
    monkeypatch.setattr("app.ingestion.host_chrome.shutil.which", lambda name: None)
    monkeypatch.setattr(Path, "is_file", lambda self: "System32" in self.as_posix())
    assert powershell_executable() == "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
