# AGENTS.md

## Purpose

This file gives AI runners a compact operating guide for working in this
workspace.

Keep runtime guidance small, practical, and verifiable. Prefer reading the
smallest useful context over loading everything.

## Required Reading

Before planning or implementation:

1. `README.md`
2. This `AGENTS.md`
3. The smallest relevant skill from `.agents/skills/`
4. The smallest relevant global instruction from `../instructions/`

Do not bulk-load all skills, instructions, plans, or source files by default.

## Skill Usage

Project-local skills live in:

```text
.agents/skills/
```

Use a local skill when it directly matches the task:

| Task type | Prefer |
|---|---|
| Browser automation, UI smoke tests, screenshots, interactive verification | `agent-browser` |
| Frontend UI, layout, visual polish | `frontend-design` |
| UX workflow, information architecture, screen density | `ui-ux-pro-max` |
| React/Vite implementation patterns | `vercel-react-best-practices` |
| Web visual design guidelines | `web-design-guidelines` |
| View transitions or route animation | `vercel-react-view-transitions` |
| User-facing wording or documentation copy | `writing-guidelines` |
| React Native work | `vercel-react-native-skills` |
| Playwright CLI for testing (can use playwright mcp) | `playwright-cli` |

Skill rules:

- Read only the relevant `SKILL.md`.
- Resolve relative paths from the skill folder.
- Use skills as focused guidance, not as a replacement for repo instructions.
- If multiple skills apply, use the smallest set that covers the task.
- State which skill is being used when it affects implementation choices.
- Use `agent-browser` for browser-based verification only when a rendered UI check is needed; do not replace unit tests or builds with screenshots alone.

## Working Principles

- Start by understanding the current code and existing patterns.
- Preserve user changes. Do not revert unrelated edits.
- Make direct edits to source files; do not create side-by-side replacement files.
- Keep changes scoped to the requested behavior.
- Prefer clear contracts and simple module boundaries over speculative abstractions.
- Keep business logic out of presentation code and route glue.
- Keep UI behavior, API contracts, and persistence concerns separated.
- Do not store secrets, credentials, tokens, or private data in source, docs, tests, or local config examples.
- Do not perform destructive filesystem, git, database, or cloud operations without explicit approval.

## Backend Rules

- Keep API routes thin.
- Put request and response contracts in versioned API schema modules.
- Put domain behavior in domain services or focused helpers.
- Keep persistence concerns in database/session/repository-style modules.
- Version public API paths.
- Do not add backward-compatible aliases unless the user explicitly requests them.
- Make local services bind to loopback by default unless the user explicitly changes the exposure model.

## Frontend Rules

- Use the existing frontend framework and component patterns.
- Keep feature code grouped by feature.
- Keep shared clients, shared types, and reusable components in shared modules.
- Use a single shared API client instead of scattering `fetch` calls.
- Keep operational apps task-focused and dense enough for repeated use.
- Avoid marketing-page structure unless the user explicitly asks for it.
- Verify responsive behavior when layout, navigation, or major visual structure changes.
- For data tables, prefer a shared table contract: columns are sortable when meaningful, columns are resizable after initial render, initial widths auto-fit content except explicitly fixed/default-width columns, vertical overflow stays inside the table visual, and horizontal scrolling sits at the bottom of the visual when rows are few.
- Table visual row density must come from shared table tokens/components, not one-off per-page padding. Override only by changing the shared density variables for a justified special case such as a drawer table.
- In data tables, long text/message columns are filled last: first auto-fit short/value columns and honor the long-text column minimum width; if the table still has spare width, expand the long-text/message column to fill the visual without creating horizontal scrolling. Use ellipsis/tooltip inside that column instead of forcing other columns wider.
- Timestamp columns should fit the formatted timestamp instead of absorbing spare table width. Horizontal scrolling is acceptable when the table has more meaningful columns than the visual can fit; do not distort short/value columns just to avoid every possible scroll case.
- For horizontal bar charts, show up to 8 bars by default; use vertical chart scrolling/dataZoom when there are more than 8 bars; keep the dimension label column width fixed while scrolling so the plot area does not resize; show tooltips only when hovering a bar value, not the whole category row; and let bar height auto-fit the available space when there are fewer than 8 bars.
- For report chart data labels, show labels opportunistically: show a value only when the mark has enough visual space, otherwise omit it. For stacked bars, require enough share within the stack and enough visible axis height/width before showing an inside label; inside labels should use white text. For labels outside bars, reserve chart headroom so labels are not clipped and use normal report text color instead of muted text.

## Web App Rules

- Keep local-first workflows easy to run.
- Keep frontend build output and backend static serving behavior aligned.
- After frontend source changes, rebuild static assets when the backend serves the built app.
- Keep development and packaged/local runtime paths clearly separated.
- Prefer explicit commands and checked outputs over assumptions.

## Verification

Choose checks based on the files changed:

| Change type | Minimum check |
|---|---|
| Text-only edit or simple low-risk fix | No test required; review the changed file or run a lightweight targeted check only when useful |
| Backend behavior | Targeted tests or direct API execution |
| Frontend behavior | Typecheck/build and a relevant UI smoke check |
| API contract | Backend test plus frontend client/build check |
| Static packaging | Build frontend and verify package/static output |
| Docs only | Review against current source paths and shipped behavior |

Do not claim completion without checked evidence.

## Reporting

Final reports should include:

- What changed.
- Verification performed.
- Known risks or skipped checks.
- Unresolved questions, if any.

Keep reports concise and factual.
