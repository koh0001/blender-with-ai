"""Build an install-from-disk Blender extension, excluding caches and secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "twin-assistant-0.1.0.zip"
destination.parent.mkdir(exist_ok=True)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for name in ("__init__.py", "metadata.py", "runtime.py", "api_runtime.py", "claude_runtime.py", "launcher.py", "actions.py", "scene_actions.py", "blender_manifest.toml"):
        archive.write(root / "twin_assistant" / name, name)
    archive.write(root / "LICENSE", "LICENSE")
    archive.write(root / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
print(destination)
