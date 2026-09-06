import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("launcher", Path(__file__).resolve().parents[1] / "blender_with_ai/launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_arguments_are_separate_no_shell_escaping(self):
        with patch.object(launcher.shutil, "which", return_value=None):
            result = launcher.build_command("C:/Test Space & Korean 한글/codex.exe", ["app-server", "-c", 'web_search="disabled"'])
        self.assertEqual(result, ["C:/Test Space & Korean 한글/codex.exe", "app-server", "-c", 'web_search="disabled"'])

    def test_windows_npm_current_and_legacy_layout(self):
        for architecture, triple in (("AMD64", "x86_64-pc-windows-msvc"), ("ARM64", "aarch64-pc-windows-msvc")):
            for legacy in (True, False):
                with tempfile.TemporaryDirectory(prefix="Codex Korean 한글 & space ") as folder:
                    base = Path(folder)
                    shim = base / "codex.cmd"
                    shim.touch()
                    package = "codex" if legacy else "codex-win32-" + ("arm64" if architecture == "ARM64" else "x64")
                    binary = base / "node_modules/@openai" / package / "vendor" / triple / ("codex" if legacy else "bin") / "codex.exe"
                    binary.parent.mkdir(parents=True)
                    binary.touch()
                    with patch.object(launcher.platform, "machine", return_value=architecture):
                        self.assertEqual(launcher._windows_npm_binary(shim), str(binary))

    def test_missing_native_binary_rejected_without_batch_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                launcher._windows_npm_binary(Path(folder) / "codex.cmd")

    def test_discovery_deduplicates(self):
        with patch.object(launcher.shutil, "which", return_value="/some/codex.exe"):
            candidates = launcher.discover_candidates()
        self.assertEqual(candidates.count("/some/codex.exe"), 1)

    @unittest.skipUnless(os.name == "nt", "Windows process creation only")
    def test_native_windows_spawn_with_spaces_and_unicode(self):
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(prefix="Codex 한글 & space ") as folder:
            binary = Path(folder) / "codex.exe"
            shutil.copy2(sys.executable, binary)
            args = launcher.build_command(str(binary), ["-c", "import sys; print(sys.argv[1])", 'a & b "quoted"'])
            env = os.environ.copy()
            env['PYTHONHOME'] = sys.base_prefix
            env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
            result = subprocess.run(args, capture_output=True, text=True, check=True, env=env, **launcher.popen_options())
            self.assertEqual(result.stdout.strip(), 'a & b "quoted"')
