# 4FusionLab add-in development

[Русская версия](DEVELOPMENT.ru.md)

## Architecture

- `CommunityStore/CommunityStore.py` and `bootstrap.py`: stable Fusion entry point and runtime selection. Both can load without `adsk`.
- `CommunityStore/runtime.py`: Fusion commands, palette and event lifecycle. This module imports `adsk`.
- `CommunityStore/config.py`: stable command identifier and product metadata. Generated once.
- `CommunityStore/core.py`: plain Python, independently testable calculation and domain logic.
- `CommunityStore/CommunityStore.manifest`: Autodesk Fusion add-in metadata; the release builder stamps the computed version into the ZIP without rewriting the checkout.
- `scripts/build.py`: deterministic archive, SHA-256, secret/symlink guardrails.
- `.github/release-paths.txt`: reviewed allowlist of files that affect the shipped runtime.

## Working on Community Store

This project is initialized. Preserve its manifest UUID and Fusion command IDs. Work on a branch, run the checks below and record native acceptance against the SHA-256 of the actual ZIP. Merging shipped inputs to `main` can publish a stable release after CI passes. Obtain the release decision before that merge.

## Local validation

The palette and native add-in messages share the English and Russian JSON catalogs in `CommunityStore/locales/`. Keep their keys and named placeholders in sync. Palette strings use the `ui.` prefix, with static labels marked using `data-i18n` in HTML. Fusion reads the add-in description directly from the manifest.

The palette defaults to the current user's OS display language and falls back to English. **Settings → Display language** can override it immediately. The preference is saved in the Fusion profile.

```bash
python scripts/check_project.py
python -m unittest discover -s tests -v
python scripts/build.py --version 1.0.0
```

An uninitialized source template may be tested using `--allow-template` but **cannot** publish; new products must not use that flag in release scripts.

## Install in Fusion

The public 1.0.0 release ships as a ZIP. To build the online installers for local testing, run the command below.

```bash
python scripts/build_installers.py --platform all
```

Go builds the downloader for both platforms. Use `--platform windows` with NSIS 3 on the PATH or `--platform macos` on a Mac with Xcode Command Line Tools. The installers and their SHA-256 files are written to `dist/installers/`.

The installer fetches the latest stable GitHub Release of `4FusionLab/CommunityStore`. Each stable release must have the standard `CommunityStore-VERSION.zip` and matching `.zip.sha256` assets. The first installation requires internet access. New Store releases do not require a new installer build.

After a successful first installation and normal exit, the Windows setup moves its own downloaded EXE to the Recycle Bin on a local fixed drive. The macOS app tries to eject the disk image it was opened from and moves that DMG to Trash. A failed or cancelled installation keeps the installer available for another attempt.

For public distribution, sign the Windows executable with Authenticode. On macOS, build with `--mac-signing-identity` using a Developer ID Application certificate, then notarize and staple the DMG. Recalculate the SHA-256 sums for the final signed files.

On a clean Fusion user profile, close Fusion and run the installer. Reopen Fusion and check **Design → Utilities → Add-Ins → Community Store**. The command should appear without manual registration or a click on **Run**. Open the catalog while signed out of GitHub and check that the command still appears after another restart. Repeat on Windows and macOS with the files downloaded through a browser. For a profile with Community Store already registered from another folder, use the existing installation to avoid duplicate registrations.

For manual ZIP installation:

1. Download the ZIP from the product's GitHub Releases, or build it locally.
2. Extract it so the Fusion API AddIns folder contains `CommunityStore/CommunityStore.manifest` and `CommunityStore/CommunityStore.py`. Do not install the ZIP as a double-nested directory.
3. Open Autodesk Fusion, go to **Utilities → Add-Ins** and start the add-in. Check the add-in entry in the SOLID **ADD-INS** panel.
4. Open **Community Store** and check that the palette lists the Fusion add-ins. Stop Community Store and verify that the palette and toolbar command disappear.
5. Repeat on macOS and Windows for OS-specific dependencies/geometry/UI, noting differences. Do not claim that CI has run native Fusion acceptance.

## Native acceptance checklist

- With **System default** selected, verify Russian and English OS display languages and the English fallback. Switch manually between both languages, check the palette labels, action messages and native toolbar tooltip, then reopen the add-in and verify that the choice persists.

- Install, start, stop and restart without duplicated controls or leaking handlers.
- In **My Add-ins**, use the folder registration button to select a compatible add-in outside the Store and Fusion API directories. Verify that Fusion lists it at its original path and that Store can control running state and startup. Check that selecting the folder again reports the existing registration and that unlinking preserves the files.
- Command can be opened, cancelled and executed; result is correct and safe.
- Active document changes, missing documents and model deletion do not cause unsafe writes.
- Test parametric and direct modeling as applicable; verify timeline updates, units, undo and redo.
- CAD API interactions, UI event order, preview lifecycle and performance are checked inside Fusion.
- Release ZIP has a valid manifest, matching GitVersion-derived version and checksum.
- Review operating system/native dependency portability and any required signing.
- Keep EN/RU documentation accurate and publish real limitations.

## Diagnostic logs

After reproducing an issue, choose **Settings → Diagnostics → Export logs…** and save the ZIP. Share it with support along with the steps to reproduce the issue and the approximate time it occurred. The Store keeps the logs locally and the user shares the ZIP themselves.

The archive contains up to four log files of 512 KiB each. Entries use UTC and include Store and Fusion versions, operation stages, and error categories and locations in Store code. The entries omit OAuth credentials, device codes, URLs, local paths, and CAD data.

If the Store cannot open, collect `community-store.log` and its numbered backups from `~/Library/Logs/4FusionLab/CommunityStore/` on macOS or `%LOCALAPPDATA%\4FusionLab\CommunityStore\Logs\` on Windows.

For native acceptance on both platforms, export after a release check, open the ZIP, and verify that it contains the run and its timestamps. Cancel a second export and confirm it creates no file.

## Optional GitHub sign-in

The Marketplace source list is saved in the local Fusion profile. It is available in both anonymous and connected mode. Under **Settings → GitHub**, select **Sign in to GitHub**, open GitHub in the browser, and enter the code shown in the palette. **Sign out** removes the local token from macOS Keychain or Windows Credential Manager. To revoke the app's authorization on GitHub, use [Authorized OAuth Apps](https://github.com/settings/applications).

The product uses the public Client ID of the Community Store OAuth App from `CommunityStore/config.py`. To distribute your own fork, register your GitHub OAuth App and enable **Device Flow**. For a local test, you can instead set `COMMUNITY_STORE_GITHUB_CLIENT_ID` in the environment that launches Fusion. An expiring token requires a new sign-in after it expires. Device Flow requires no embedded client secret and the Store requests no scopes. [GitHub Device Flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow)

On macOS and Windows, verify anonymous browsing, approval, cancellation, and persistence after restarting Fusion. Check that signing out returns to anonymous browsing with the same sources. Revoke authorization on GitHub and verify that the next catalog check returns to anonymous mode. Check that an anonymous rate-limit pause does not block a connected check, and that ZIP and image downloads carry no OAuth token.

Settings shows the authenticated account's name, login, and avatar. Signing in uses the authenticated GitHub REST API quota for public catalog requests. [REST API limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)

## Reporting add-ins and sources

In the add-in details and the catalog's Sources dialog, open the report dialog. Verify that Cancel returns to the same view, and that opening the public draft launches GitHub with a prefilled draft in the selected language. The add-in draft includes the public catalog repository and its displayed release tag when available. The source draft includes the selected GitHub owner. Both drafts include the Store version. Review the draft in the browser and leave it unsubmitted during acceptance. The security option opens the localized [security policy](../SECURITY.md) with private contact instructions.

GitHub loads the four issue forms from `.github/ISSUE_TEMPLATE/` on the default branch. Keep the field IDs in the forms aligned with the query parameters in `CommunityStore/reporting.py`.

## Community Store self-update

The existing release check schedule also checks the latest stable release in the public `4FusionLab/CommunityStore` repository. When the release is newer than the running version, select **Update to** in the Community Store row under **My Add-ins**. Store downloads `CommunityStore-<version>.zip` and verifies the GitHub asset SHA-256, manifest identity and version, and OS compatibility.

Install a build with the loader once. With **Run on Startup** enabled, the next Fusion launch tries the staged version before importing the main code. A failed or interrupted start restores the last successful version, with the original bundled runtime available as a fallback. The registered entry point and manifest stay in place, so the version shown in Fusion's native manager can be the original installed version. The Store palette shows the actual running version. The Fusion profile and `CommunityStore/AddIns` retain their existing paths.

Git worktrees and linked directories use their local code. A future change to the loader itself requires a manual installation. The `runtime.run` contract and update-state format must remain compatible with previously shipped loaders.

For native validation, use a standalone test installation with **Run on Startup** on. Verify first start, staging a newer test version, activation after restarting Fusion, and rollback from a test runtime that raises during startup. Confirm that the settings, registration path and managed add-ins survive each transition. Repeat on macOS and Windows.

## Release policy

The shared Actions are pinned to 4FusionLab/.github commit `2e961620a068c3ad754f6e1be05fb6e4ac68e843`. The org's [RELEASE_PLATFORM.md](https://github.com/4FusionLab/.github/blob/main/RELEASE_PLATFORM.md) is authoritative. Product code changes are eligible when their filenames match `.github/release-paths.txt`; all required cross-platform tests and ZIP validation must pass before publication.

Community Store is distributed under the [PolyForm Noncommercial License 1.0.0](../LICENSE). The installable ZIP includes the full terms in `CommunityStore/LICENSE`. The bundled Lucide icons and markdown-it library retain their [third-party notices](../CommunityStore/ui/vendor/).
