# Project Guidelines

## Language
ALL commits, code comments, and documentation MUST be in English.

## Commit Format
Use conventional commits format. See recent commits for examples:
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `chore:` maintenance
- `refactor:` code refactoring

Rules:
- One-liner commits only (no multi-line messages)
- No signatures or Co-Authored-By
- Compare with recent commits to match the style
- Scope is optional but recommended: `feat(settings): add incognito browser configuration`

## Versioning
- Active line: `1.1.x`. Increment sequentially for each release: `1.1.2`, `1.1.3`, … — plain patch numbers, no `-alpha`/prerelease suffix.
- Never revert to `1.0.0-alpha*`.
- Bump the version in both `pyproject.toml` and `electron-app/package.json`.

## Code Style
- Python codebase
- Follow existing patterns in the project
- Keep code simple and readable
