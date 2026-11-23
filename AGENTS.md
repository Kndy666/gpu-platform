# Project conventions

- Use the GitHub MCP connector for repository pushes.
- Follow `docs/en/architecture.md` and `docs/en/modules.md` for the modular control, panel, node, installer, and slot architecture.
- Write direct, concrete prose. Avoid defensive framing and contrast formulas such as “不是……而是……” or “not X, but Y”.
- Use spacing, alignment, grouping, and visual hierarchy to communicate frontend structure. Keep secondary headings sparse and purposeful.
- Test observable behavior, contracts, resource accounting, authorization, state transitions, and failure handling. Use portable fixtures and injected dependencies so tests run independently of a particular deployment or this machine.
- Organize tests around service functionality and contracts. After fixing a bug, use existing functional tests and direct functional checks to verify the fix; do not add bug-specific regression tests.
- Do not make preservation of old features a test objective. Do not assert prose, translated strings, page copy, or textual snapshots. These testing rules take precedence over the architecture document's old/new snapshot comparison proposals.
- Keep user-facing source strings in English and provide Simplified Chinese translations. Agent skills, installer output, logs, and configuration comments use English.
- Modules depend on the contracts package and their own implementation dependencies. Load implementations through entry points. Keep concrete infrastructure out of control and node framework code.
- Keep the project free of application migration history and compatibility adapters. New deployments create the current schema directly. No legacy deployment conversion tool is required for this refactor.
- Remove temporary files, generated test resources, and superseded code when the refactor is complete. Preserve user data and pre-existing resources.
- Do not keep agent-generated verification records, acceptance reports, or scratch notes in the repository. Report verification results in the conversation.
