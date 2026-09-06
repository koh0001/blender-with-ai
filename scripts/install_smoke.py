"""Install, enable, exercise and remove the ZIP using temporary Blender preferences."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--blender', default='blender')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
archive = root / 'dist/twin-assistant-0.1.0.zip'
if not archive.is_file():
    raise SystemExit('Run python scripts/package.py first.')
with tempfile.TemporaryDirectory(prefix='blender-addon-install-') as folder:
    base = Path(folder)
    env = os.environ.copy()
    for variable, directory in [('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                                ('BLENDER_USER_EXTENSIONS', 'extensions')]:
        path = base / directory
        path.mkdir()
        env[variable] = str(path)
    def run(*command):
        result = subprocess.run([args.blender, '--background', *command], env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        print(result.stdout, end='')
        if result.returncode:
            print(result.stderr)
            raise RuntimeError('Blender install verification failed')
    run('--command', 'extension', 'validate', str(archive))
    run('--command', 'extension', 'repo-add', 'smoke_local', '--directory', str(base / 'repo'), '--clear-all')
    run('--command', 'extension', 'install-file', '--repo', 'smoke_local', '--enable', str(archive))
    probe = base / 'probe.py'
    probe.write_text("import bpy\nassert hasattr(bpy.types.WindowManager, 'twin_provider')\nassert hasattr(bpy.ops.twin, 'propose')\nassert 'bl_ext.smoke_local.twin_assistant' in bpy.context.preferences.addons\nprint('INSTALLED_EXTENSION_ENABLED_OK')\n")
    run('--python-exit-code', '1', '--python', str(probe))
    run('--command', 'extension', 'remove', 'smoke_local.twin_assistant')
    if (base / 'repo/twin_assistant').exists():
        raise RuntimeError('Installed extension was not removed')
print('INSTALL_SMOKE_OK')
