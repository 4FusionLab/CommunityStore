# Community Store development contract

[Русская версия](AGENTS.ru.md)

Read [4FusionLab shared engineering rules](https://github.com/4FusionLab/.github/blob/main/AGENTS.md) and [the release platform](https://github.com/4FusionLab/.github/blob/main/RELEASE_PLATFORM.md) first.

## Working model

- The Fusion Python package is `CommunityStore/`.
- Discuss requirements and plans in Russian. Use clear English for Fusion commands, controls, diagnostics, and stable API names. Write documents in English and maintain separate Russian versions of documentation; intermediate working notes do not require translation.
- For README creation or edits, apply the [shared product README guidelines](https://github.com/4FusionLab/.github/blob/main/README_GUIDELINES.md). Check both languages, current release and license terms, and actual validation evidence before delivery.
- Treat the official Autodesk Fusion API documentation as the source of truth. Never invent SDK symbols; do not imply successful CAD acceptance based solely on offline CI.
- Put calculations and domain rules into `CommunityStore/core.py` or modules that **do not import adsk**. Fusion API interactions belong in adapters, event handlers and commands running in Fusion's permitted execution context.
- Design commands to be safe: validate active document, entities, units, parameters, and timeline state; use native previews where applicable. Never modify a user's CAD data during tests or without an explicit command action.
- Retain event handler instances while active; detach UI controls and command definitions in `stop(context)`. Keep identifiers stable after initial scaffolding. Avoid global state tied to stale documents.
- Add meaningful unit and offline integration tests. Test real Fusion flows on Windows/macOS separately when they matter; report what has and has not actually been verified.

## Supplier guide

- Read the supplier guide linked from the [English README](README.md) and [Russian README](README.ru.md) before changing repository discovery, installation metadata, supported releases or ZIPs, README rendering and media, or localization.
- Compare affected guide sections and examples with the implementation and tests. Update both language versions in the same change whenever supplier-facing behavior changes. Keep the instructions and examples equivalent across languages.
- During release verification, compare the guide with the published build. Label behavior that requires a newer Community Store version and remove outdated prerelease notes when the corresponding build is published.

## Versioning and publishing

- `python scripts/init_addin.py --name NewAddin` is a **one-time** setup; never regenerate an existing product manifest UUID or command IDs.
- Version uses GitVersion.Tool **6.8.2**, Conventional Commits and the shared pinned Actions. Direct push or PR merge into `main` triggers a release **only** for shipped paths listed in `.github/release-paths.txt`.
- All checks must pass before publish. Documentation/tests/CI edits do not publish a new release. Do not create tags manually, overwrite published ZIPs or move existing tags.
- Only the publishing job has `contents: write`. Package ZIP, embedded manifest version, checksums and release notes must agree. The ZIP must install as a Fusion Add-in folder.
- Do not commit credentials, private CAD files, signing keys, platform-generated artifacts, or user paths. Review repository licensing before public distribution.

## Commands

- Offline tests: `python -m unittest discover -s tests -v`.
- Project validator: `python scripts/check_project.py`.
- Product ZIP: `python scripts/build.py --version 0.1.0`.
- Manual CAD acceptance checklist: `docs/DEVELOPMENT.md`.
