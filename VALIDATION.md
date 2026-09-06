# Validation — 2026-09-06

## Verified on macOS

Environment: Blender 5.2.1 LTS, Codex CLI 0.153.4, Claude Code 2.1.263.

- Offline Python tests cover command validation, Codex protocol, Claude CLI transport, OpenAI/Anthropic API contracts, cancellation and Windows launcher layouts.
- `blender_actions_smoke.py`: all five primitives; move, rotate, scale, rename; stale or locked object rejection; rollback after injected mid-batch failure.
- `blender_chat_smoke.py`: UI registration, model selection, chat create and follow-up move, actual result history, stale scene/collection rejection, cancellation of a queued result, pure chat, provider switching and cleanup.
- Uncompressed saved `.blend` was inspected: distinctive chat, prompt and API-key test values were absent. These values use WindowManager session state, not Scene custom properties.
- Separate factory-startup GUI process: a timer-triggered chat operator created an object; Blender Undo removed it.
- `blender_smoke.py`: legacy metadata regression checks remain passing.
- ZIP manifest validation, installation in isolated preferences, enabled add-on startup and removal passed through `install_smoke.py`.

## Live account requests

Only synthetic empty scenes were used. No private model or project data was sent.

- ChatGPT/Codex `gpt-5.6-luna`: created a named cube, then moved that selected cube by X=2 in a follow-up turn.
- Claude Code subscription `sonnet` alias: the same create/follow-up move passed.
- One initial Codex follow-up returned a target outside the selection and was rejected without executing it. A subsequent full run passed. Model output remains subject to validation; natural-language requests are not guaranteed to produce an accepted command.
- Existing account sessions were reused. Fresh browser login completion, cancelled browser authentication and Windows account authentication have not been manually exercised.

The opt-in live test consumes model usage:

```
blender --background --factory-startup --python-exit-code 1 --python scripts/blender_live_smoke.py
blender --background --factory-startup --python-exit-code 1 --python scripts/blender_live_smoke.py -- --provider claude
```

## API keys

OpenAI Responses and Anthropic Messages adapters were checked against official documentation and mocked HTTP responses. No real API-key inference was run. Invalid keys, quotas, model availability and provider-specific limits can still reject a request. API cancellation discards a late response; the underlying HTTP call may remain pending until its timeout.

## Windows and compatibility

GitHub Actions runs Python tests on Windows, macOS and Linux. A separate Windows job downloads official Blender 4.2.0 and runs scene/chat integration and ZIP validation. Record the actual workflow result before claiming a Windows pass.

Windows CLI launch avoids npm batch-file quoting by resolving the native Codex executable. Windows process tests include spaces, Korean characters and shell metacharacters in paths/arguments. Claude account mode expects the native Claude Code executable.

Manual Windows GUI layout, Undo and fresh login remain unverified. Blender 4.2 is the declared minimum; macOS GUI verification used 5.2.1.

## Current limits

The command vocabulary covers primitive creation and selected object transforms/rename only. No arbitrary Python, delete, file operations, edit-mode mesh tools, modifiers, materials or animation editing. Maximum 20 commands per response and 200 selected objects. Conversation length is bounded to ten completed exchanges; start a new chat when full. Names are bounded to 63 UTF-8 bytes. Transform locks, constraints and animation can cause an operation to be rejected.
