# First release decision

[Русская версия](FIRST_RELEASE.ru.md)

## Windows x64 community test candidate

**Candidate 0.2.0 is ready for community testing in Fusion for Windows x64.** The installable ZIP has SHA-256 `4b2004162db0dec62fc1c4649fc61c1af33040aae3a176454ea050c0c3e37536`. The tester kit contains the ZIP, checksum and guides in English and Russian. [Windows scenarios and reporting](WINDOWS_COMMUNITY.md)

On macOS, 111 of 115 offline tests passed on each of Python 3.12 and 3.14. Three Windows tests and one template test were skipped. All 20 Chrome palette tests passed. With Windows package selection enabled, the extracted candidate downloaded, verified by SHA-256 and staged three public ZIPs, finding the entry points for ACDC4Robot 1.1.0, 1.1.1 and Gridfinity 2.0.0. Registration and startup in Windows Fusion, NTFS file locks and sign-in persistence through Windows Credential Manager await community testing. Native macOS results below refer to the builds identified in each section.

## Full visual acceptance on macOS

**The 0.2.0 interface passed visual acceptance in Fusion 2706.1.17 on macOS 27.0.1.** SHA-256 `c2a295e45c20bb07d7ae4084b1dfb3476213b99a822f2feddb0f42d4b0d1e541`.

The isolated Fusion palette rendered 41 screens and states in English and Russian in light and dark mode. Each of the 164 combinations has paired before and after captures with controlled fixture data. The content viewport measured 314 CSS px. Two compact cases used a 300 CSS px frame in the same native palette. All 164 final captures inherited their theme from macOS. An already open WebView retained its previous appearance until recreated.

The folder picker and save-log dialog opened and were cancelled in both system themes and both Store languages. Files and registrations were preserved. Restarting the add-in refreshed the translations in the normal checkout-backed palette and preserved GitHub sign-in and Run on Startup.

The changes address version alignment, wrapping, menu placement, keyboard focus, labels and action grouping. Browser coverage adds 492 screenshots of the final build and 20 passing tests. Of 92 offline tests, 91 passed and one template test was skipped. Five interface and localization files changed in the ZIP. The previous installation, data safety and GitHub evidence below applies to the builds identified there.

## Previous targeted retest

The visual review of the installed add-in list was reopened. The previous 4/4 rating missed a low chevron in the target-version button and a split Russian label at intermediate widths. The revised candidate is 0.2.0, SHA-256 `c15d59ab359d22f98d636d6ce4081a4178dcfb01a00efe968986d5c96e89033f`.

**The corrected macOS candidate passed the targeted retest.** The browser matrix covered seven widths from 320 to 960 px, both languages and both themes. All 15 browser tests passed. In an isolated native Fusion 2706.1.17 palette both buttons opened the version picker, and saving v0.0.2 updated the row and test profile. The ZIP differs from the previous candidate in three palette files. The earlier full acceptance evidence below applies to its recorded digest.

## Previous candidate

**Decision — candidate 0.2.0 is accepted for the first macOS release.** The owner excluded Windows from this acceptance run. The local candidate is 0.2.0, dated 25 November 2026, SHA-256 `ad14c7810812672982280c370970eab76bae632e94ca77f6471cf027075f2a26`.

| Acceptance area | Evidence and limits |
|---|---|
| Packaging | Two independent checkouts produced the same 44-file ZIP. SHA-256, CRC, entry point, manifest UUID and version, startup setting, product license and vendor notices passed. [Package checks](../../tests/test_package.py) |
| Catalog and real releases | The anonymous client discovered public packages from 4FusionLab, the ACDC4Robot organization and the 906grown user. In real Fusion using an isolated Store profile, the Store installed gridfinity_v2 2.0.0 from the user source and upgraded a temporarily registered ACDC4Robot 1.1.0 to 1.1.1. Both remained running with startup enabled after their sources were removed. Archiving them removed their commands and registrations. |
| File safety | Tested digest and manifest rejection, rollback after registration failure, preservation of user files, and restoring a backup after interrupting a managed update. Recovery from a process interruption requires the user steps in the README. [Release tests](../../tests/test_releases.py) · [Integration tests](../../tests/test_store_integration.py) |
| Native macOS | Fusion 2706.1.17 on macOS 27.0.1 arm64. The isolated native series covered the first-run guide, search, removing a source while keeping its installed add-in, local registration and a two-add-in update with different startup settings. GitHub metadata and packages were controlled fixtures. The normal command in the user's working checkout also reopened its palette and created a fresh WebView. |
| Interface | All 14 browser checks passed against the final ZIP, including EN/RU and light/dark themes at 300, 320 and 620 px. An independent review scored the tested scenarios 4/4 after verifying keyboard access from the first onboarding step. [Browser checks](../../tests/test_palette_browser.cjs) |
| GitHub | The final ZIP completed real GitHub Device Flow. After stopping and restarting the add-in, the profile and seven cards returned. Adding a public organization and user brought the catalog to nine cards. Sign-out removed the isolated Keychain item, restored the sign-in control and preserved the sources. |
| Offline regression | Python 3.12 and 3.14 on macOS each passed 91 tests, with one template-only skip. Coverage includes network failures, isolated quotas, paginated catalog resumption and protected removal archives. [Tests](../../tests) |

Clean installation with the original UUID and two consecutive full Fusion launches passed using the same test Store profile. Process 7152 saved guide completion and installed Gridfinity v2 2.0.0. In process 7434 the Store started automatically, the guide stayed dismissed and Gridfinity was running with startup enabled. In this series the test used the native dismissGuide handler after an automated click missed the floating palette. A separate native UI run exercised the first-step action. The original profile, registration, account, other add-in states and document version were restored. Publication requires a separate instruction.
