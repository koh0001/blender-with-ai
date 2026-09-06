# Development guide

This is a Blender add-on for controlling the scene through an AI chat panel. Keep the common add-on independent of company projects. Domain-specific metadata or digital-twin workflows belong in downstream forks or extensions.

## Architecture

- `twin_assistant/__init__.py`: Blender UI, session state, main-thread operators.
- `actions.py`: pure Python command schema and strict response validation.
- `scene_actions.py`: bounded Blender operations, freshness checks, rollback.
- Runtime modules: provider transport only; never import `bpy` or mutate scenes.
- `launcher.py`: cross-platform local CLI discovery.

## Changes

- Support Windows and macOS; maintain the declared Blender minimum version.
- Use Python's standard library unless a dependency is explicitly justified.
- Keep UI responsive: network and CLI work happen off Blender's main thread.
- Never execute model-generated Python, shell commands, or arbitrary tools.
- Validate a complete action batch before changing the scene. Preserve atomic rollback, stale-state rejection, cancellation, and Blender undo.
- Keep credentials, prompts, and chat history out of `.blend` files, preferences, logs, and Git. API keys belong only in session memory or environment variables.
- Authentication uses official provider CLI/API flows. Do not copy account tokens or silently switch account login to API billing.
- Consult official documentation or installed CLI/schema documentation when changing provider protocols. Do not guess endpoints or flags.
- Do not publish private project documents, user data, generated test scenes, credentials, or operational agent state.

## Verification

Run `python -m unittest discover -s tests -v` and `python scripts/package.py`.

Run each offline Blender smoke script with an empty scene:

```
blender --background --factory-startup --python-exit-code 1 --python scripts/blender_actions_smoke.py
blender --background --factory-startup --python-exit-code 1 --python scripts/blender_chat_smoke.py
blender --background --factory-startup --python-exit-code 1 --python scripts/blender_smoke.py
```

`blender_live_smoke.py` is opt-in: it uses an existing account and consumes model usage. Do not run live requests in CI or with private scene data. Record actual tested OS, Blender, provider, and CLI versions in `VALIDATION.md`; distinguish mocked tests from live inference and GUI tests.

Before completion, review the final diff separately from authoring. Do not claim Windows GUI or real provider authentication passed based only on mocks. No skipped placeholder tests or unfinished branches as evidence of completion.
