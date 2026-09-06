# Claude Code guidance

Read and follow [AGENTS.md](AGENTS.md) for architecture, implementation boundaries, and verification commands. Read [README.md](README.md) for the user workflow and [VALIDATION.md](VALIDATION.md) for known test coverage.

This repository develops Blender with AI, the common Blender AI control add-on. Keep organization-specific work in downstream projects. Use official Claude Code login for account mode and Anthropic API keys only when API mode is explicitly selected. Never expose, persist, or copy authentication material.

Model output is data consumed by the validated Blender action vocabulary. It is never Python or shell code to execute. Scene changes must stay on Blender's main thread and remain undoable.
