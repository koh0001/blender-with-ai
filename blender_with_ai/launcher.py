"""Find and launch Codex without invoking a shell, including npm Windows shims.

npm layout verified against @openai/codex 0.153.4 bin/codex.js. Native Windows
binaries are preferred so terminating the app-server also terminates the process
that owns its pipes. No cmd.exe quoting or batch-file execution is required.
"""
import os
from pathlib import Path
import platform
import shutil
import subprocess


def discover_candidates():
    candidates = []
    for name in ("codex.exe", "codex.cmd", "codex") if os.name == "nt" else ("codex",):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    home = Path.home()
    if os.name == "nt":
        paths = [home / ".local/bin/codex.exe", home / ".cargo/bin/codex.exe"]
        for variable, suffix in (("APPDATA", "npm/codex.cmd"),
                                 ("LOCALAPPDATA", "Microsoft/WinGet/Links/codex.exe"),
                                 ("LOCALAPPDATA", "Programs/Codex/codex.exe")):
            if os.environ.get(variable):
                paths.append(Path(os.environ[variable]) / suffix)
    else:
        paths = [home / ".local/bin/codex", Path("/opt/homebrew/bin/codex"),
                 Path("/usr/local/bin/codex"), home / ".cargo/bin/codex"]
    candidates.extend(str(path) for path in paths if path.is_file())
    return list(dict.fromkeys(candidates))


def _windows_npm_binary(shim):
    """Resolve standard global/local npm and bundled vendor package layouts."""
    base = shim.parent
    roots = [base / "node_modules/@openai/codex", base.parent / "@openai/codex"]
    if shim.suffix.lower() == ".js":
        roots.insert(0, base.parent)
    architecture = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    triple = "aarch64-pc-windows-msvc" if architecture == "arm64" else "x86_64-pc-windows-msvc"
    for root in roots:
        vendor_roots = [root.parent / f"codex-win32-{architecture}" / "vendor",
                        root / "node_modules/@openai" / f"codex-win32-{architecture}" / "vendor",
                        root / "vendor"]
        for vendor in vendor_roots:
            # Current layout uses bin; earlier versions used codex.
            for directory in ("bin", "codex"):
                native = vendor / triple / directory / "codex.exe"
                if native.is_file():
                    return str(native)
    raise RuntimeError("Codex Windows 실행 파일을 찾지 못했습니다. @openai/codex를 다시 설치하거나 codex.exe 경로를 지정해 주세요.")


def build_command(codex_path, arguments):
    path = os.path.expanduser(os.fspath(codex_path))
    resolved = shutil.which(path)
    if resolved:
        path = resolved
    if os.name == "nt":
        suffix = Path(path).suffix.lower()
        if suffix in (".cmd", ".bat", ".ps1", ".js") or not suffix:
            path = _windows_npm_binary(Path(path))
        elif suffix != ".exe":
            raise RuntimeError("Windows에서는 Codex 실행 파일(.exe) 또는 npm codex.cmd 경로를 지정해 주세요.")
    return [path, *arguments]


def popen_options():
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if os.name == "nt" else {}
