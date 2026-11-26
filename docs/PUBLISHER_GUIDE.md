# Publish a Fusion add-in for Community Store

[Русская версия](PUBLISHER_GUIDE.ru.md) · [Community Store](../README.md)

This guide is for authors and distributors who already build an Autodesk Fusion add-in. Use it to make a GitHub repository discoverable and give users an installable release and a useful Marketplace page.

**Release status.** As of November 26, 2026, Community Store has no published GitHub Release. This guide describes the current development build, including localized README loading, the Marketplace detail page, and the installed add-in version list. [Community Store releases](https://github.com/4FusionLab/CommunityStore/releases)

## Connect the GitHub owner and make the add-in discoverable

1. Use a public repository owned by your GitHub user or organization. Community Store skips private, archived, and forked repositories, and repository names starting with a dot.
2. For a GitHub owner outside the default list, tell users to open **Marketplace → Sources** and enter the owner's login, profile URL, or repository URL. For example, `https://github.com/ExampleStudio/PanelTool` adds all eligible repositories owned by `ExampleStudio` to that installation. Public browsing works signed out. Signing in uses the user's GitHub API quota.
3. Publish a GitHub Release. A Git tag alone or a draft release will not create a card. Declare the add-in explicitly with one of the following repository topics — `fusion-addin`, `fusion360-addin`, `autodesk-fusion-addin`, or `fusion-360-addin`.

Community Store also recognizes an ordinary Fusion add-in in the selected tag's file tree. It looks for a JSON `.manifest` beside a same-stem `.py`, `.ts`, `.js`, `.dll`, `.dylib`, or `.cpp` file. Use `"type": "addin"` and `"autodeskProduct": "Fusion"` or `"Fusion360"`. Keep the manifest outside `test`, `tests`, `samples`, `examples`, and `docs`. Keep it at or below 32 KiB and easy to find. The Store checks up to six candidates, preferring the repository's name, then shallower paths.

Without a recognized topic or tagged manifest, the Store can discover an add-in by inspecting an eligible uploaded ZIP with a GitHub digest and a supported version tag. A topic can get a release listed without a valid installable package. Keep the tagged manifest even when using a topic so the Store can identify the add-in by ID and discover its bundled artwork.

## Request inclusion as a trusted source

To list your GitHub profile or organization by default, open a public request with links to the owner and an example add-in release. Community Store maintainers review the request. Once approved and added, eligible add-ins from that owner appear in the catalog. [Request trusted source status](https://github.com/4FusionLab/CommunityStore/issues/new?template=request-source.yml)

Community Store maintainers manage default owners in [trusted-stores.json](../trusted-stores.json). After approving a request, maintainers add the GitHub login to the `owners` array and merge the change into `main`. This file is outside the add-in release paths, so a change to this file alone updates the directory without publishing a new Store release. Community Store checks for changes at launch, when the palette opens, and during catalog checks. It keeps the last valid list for offline use and preserves manually added sources. A fresh installation uses `4FusionLab` until it retrieves a valid list.

## Package and publish a release

1. Choose a tag such as `v1.2.0` and embed `"version": "1.2.0"` in the ZIP manifest. The Store compares numeric versions. It accepts two to four numeric components, an optional `v` prefix, and optional `+build` metadata, which is ignored when comparing versions. Tags such as `v1.2.0-beta.1` do not qualify for installation. Mark previews as pre-releases on GitHub. They can appear in Marketplace with a GitHub link, while Store installation and version targets use stable releases.
2. Upload your installable `.zip` under **Assets** in the GitHub Release for that tag. GitHub's automatic **Source code (zip)** is excluded. The Store requires the SHA-256 digest in GitHub's asset metadata and verifies the downloaded bytes. A separate `.sha256` file can help manual verification but cannot supply the Store's required digest. [GitHub release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
3. Include one add-in folder with its matching manifest, entry point, and runtime dependencies. Use the same spelling and case for the folder and file stems for portability. Use a unique, stable GUID in `id` and keep it consistent between the tagged manifest, each asset, and later versions. The Store accepts `.py`, `.ts`, or `.js` as script entry points, `.dylib` on macOS and a 64-bit AMD64 PE DLL on Windows. A `.cpp` source file can help discovery but cannot serve as an installable entry point. The archive limits are 150 MiB downloaded, 450 MiB uncompressed, and 15,000 entries. Use ordinary relative paths with no symlinks, case-only collisions, or Windows-reserved names. [Autodesk manifest and add-in layout](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/WritingDebugging_UM.htm)
4. Declare the systems that the package actually supports in `supportedOS` using `windows`, `mac`, or `windows|mac`. The Store checks the asset filename before download and the manifest and entry point during staging. A compatible label in the picker does not verify that the binary or its dependencies will run in Fusion.

| Package | Example uploaded asset |
| --- | --- |
| Same files for Windows and macOS | `PanelTool-1.2.0.zip` |
| Windows x64 | `PanelTool-1.2.0-windows-x64.zip` |
| macOS Apple silicon | `PanelTool-1.2.0-macos-arm64.zip` |
| macOS Intel | `PanelTool-1.2.0-macos-x64.zip` |
| Universal macOS binary | `PanelTool-1.2.0-macos-universal2.zip` |

Separate platform and architecture tokens with hyphens, underscores, or dots. The Store recognizes `mac`, `macos`, `darwin`, `osx`, `win`, `windows`, `win64`, and `windows64`. Use `x64` (also `amd64`, `intel`, `x86_64`) or `arm64` (also `aarch64`) with an explicit OS token. Windows package selection expects x64. macOS selection checks the architecture reported by the Store's process. ZIPs marked as source, debug, Linux, or 32-bit builds are ineligible. When multiple ZIPs are listed, the picker marks which ones are eligible on the current system. Verify your actual package on every supported Fusion platform.

## Link an existing installation

For a manually installed or cloned add-in, first register its folder in Fusion so it appears in **My Add-Ins**. Add the GitHub owner to **Sources**. Community Store uses a saved repository association for its own installs or the local checkout's GitHub `origin` for version lookup. With neither, it looks for a unique catalog match.

A matching manifest ID is the strongest automatic match. The Store can also match by add-in name, using the manifest stem when discovered or the repository name otherwise, or by GitHub `origin`. When both IDs exist and differ, the same name cannot establish a match. For folder updates without an ID, an exact GitHub `origin` is required and the archive must match the add-in name. Use a stable ID and a consistent name to avoid ambiguous matches. Matching an installation can mark it **Installed** in Marketplace. Applying a package update is a separate action.

## What users see

| Field | Source |
| --- | --- |
| Name, publisher, stars | Marketplace uses the GitHub repository name, owner login and repository star count. The catalog sorts by stars. The local detail view uses the name reported by Fusion. |
| Publisher and author in the lists | Marketplace shows the GitHub owner and, when available, the release manifest author. My Add-ins shows the author reported by Fusion and the GitHub owner if the installation is linked to a repository. |
| Description | GitHub repository description, falling back to the first usable line of the selected release body. The card shows a short excerpt without Markdown formatting. |
| Author and installed version | The detail header prefers the author reported by the local Fusion registration. It also shows the manifest's `author` when the Store has discovered it for the selected release. The installed version comes from the installed manifest, with the Fusion registration as fallback. |
| Repository version and publication date | The selected GitHub Release's tag and publication date. |
| Release notes | The selected GitHub Release's body, rendered as Markdown up to 16,000 characters. Put the summary first. The release title does not replace the tag. |
| Version targets | In **My Add-Ins → Target** or in the detail header, a matched installation can follow **Latest** or choose an available stable tag from release history. The list is sorted by numeric version and filters for a compatible ZIP with a GitHub digest. History loads in the background and cached versions may be shown when GitHub is unavailable. |

The Store starts with GitHub's latest stable release. If that release cannot be installed on the current system, it selects the first eligible stable release returned by release history. If no eligible release exists, the latest release or the first published history entry can still appear without an install action. Both the README and the release notes in the detail page follow the release selected for Marketplace, even if a user chooses a different installed add-in target.

## Write the Marketplace README

Put UTF-8 `README.md` and `README.ru.md` in the repository root **at the release tag**. The Store does not fetch from the default branch when rendering a release. English reads `README.md` only. Russian tries `README.ru.md` first, then `README.md` if the Russian file is missing, zero-length, or otherwise unavailable. A whitespace-only Russian file can suppress the fallback and show the README as unavailable. The limit is 96 KiB per file. Use the exact filename case and include both versions and their images before tagging.

**Settings → Display language** selects English, Russian, or the system display language. Unsupported system languages fall back to English. This setting controls the Store interface and README selection. Localize your own Fusion commands and controls separately. Keep the manifest's localized descriptions for Fusion and write GitHub descriptions and release notes in the languages you want readers to see. The Store displays those GitHub fields as supplied, regardless of the display language.

The detail page shows **Read README** and a link to the selected file on GitHub. It indicates loading and unavailability. Headings, paragraphs, lists, emphasis, blockquotes, code, and tables are supported. A limited set of HTML elements such as `<details>` and `<summary>` is also supported. Interactive embeds, scripts, styles, and most HTML attributes are removed. Use explicit Markdown links. HTTPS links open in the browser, and relative links, including fragment links, resolve to the tagged file on GitHub. Plain URLs are not automatically linked. Release notes use the same text renderer, with relative links based on the tagged `README.md` and no inline image loading.

### Logos and screenshots

- Use PNG, JPEG (`.jpg` or `.jpeg`), or WebP images, up to **1 MiB** each and **4096 pixels** on either side. Use ordinary relative paths in direct Markdown image syntax or a quoted HTML `<img src="…">` at the selected tag. External images, stock badges, SVGs, and GIFs cannot load inline. A Markdown reference definition alone does not register an image for loading. The renderer uses the alt text when an image is unavailable.
- For a README image to double as a screenshot, put it in a folder such as `media/screenshot.png`. A root-level image or one whose filename contains `icon` or `logo` is treated as a logo candidate. README images and attached images whose names contain `badge`, `banner`, `avatar`, or `shield` are skipped. The Store considers up to 12 distinct README image paths and attempts to display up to four inline, considering screenshots before logo candidates.
- The logo priority is a declared icon, then a release image asset with `icon` or `logo` in its name, then one of the first two ranked bundled PNGs under the tagged add-in's `resources/`, then a README logo candidate. The bundled names are `32x32@2x.png`, `64x64.png`, and `32x32.png`. The screenshot order is declared images, other eligible release image assets, then README screenshots. Up to eight eligible release image attachments are considered. The detail page shows up to three screenshots. The Marketplace card shows the first screenshot, then falls back to the logo or a placeholder.
- For explicit artwork, add a root `community-store.json` at the tag with relative raster paths. Use `icon`, `preview`, and up to six paths in `screenshots`. The Store reads this config when it finds it alongside a recognized manifest in the tagged tree search. The config and release attachments are shared across display languages. Use images in the localized READMEs if you want the copies to reference different illustrations.

## Minimal example

A fictional `ExampleStudio/PanelTool` repository at tag `v1.2.0` can have this layout. Use your real add-in files and images.

```text
README.md
README.ru.md
media/
  logo.png
  screenshot.png
PanelTool/
  PanelTool.manifest
  PanelTool.py
```

`PanelTool/PanelTool.manifest`

```json
{
  "autodeskProduct": "Fusion",
  "type": "addin",
  "id": "cee740c8-fe09-4e15-abc7-0fdc760a23f9",
  "author": "Example Studio",
  "description": {
    "": "Create perforated panels.",
    "1049": "Создавайте перфорированные панели."
  },
  "version": "1.2.0",
  "runOnStartup": false,
  "supportedOS": "windows|mac"
}
```

`README.md`

```markdown
# PanelTool for Autodesk Fusion

[Русская версия](README.ru.md)

Create perforated panels with a configurable hole pitch.

![PanelTool logo](media/logo.png)

## In Fusion

Open **PanelTool**, set the pitch, and preview the hole pattern.

![Preview of a perforated panel](media/screenshot.png)
```

`README.ru.md`

```markdown
# PanelTool для Autodesk Fusion

[English](README.md)

Создавайте перфорированные панели с настраиваемым шагом отверстий.

![Логотип PanelTool](media/logo.png)

## В Fusion

Откройте **PanelTool**, задайте шаг и посмотрите предварительную раскладку отверстий.

![Предпросмотр перфорированной панели](media/screenshot.png)
```

Publish a stable GitHub Release tagged `v1.2.0` with the notes “Added a hole-pattern preview. / Добавлен предварительный просмотр раскладки отверстий.” Upload `PanelTool-1.2.0.zip` containing `PanelTool/PanelTool.manifest` and `PanelTool/PanelTool.py`, plus any dependencies your add-in needs. Confirm GitHub shows the ZIP's digest. For a real release with an uploaded installable ZIP and digest, see [ExporTo v1.2.0](https://github.com/4FusionLab/ExporTo/releases/tag/v1.2.0).

## Check the result

- **No card.** Check the owner's login in **Sources**, repository visibility, the published release, and the topic or manifest evidence at the selected tag. Refresh the catalog. If GitHub has limited requests, check the retry time or sign in.
- **Card with no install action.** Open the tagged release on GitHub. Check that it is stable, its tag parses as a version, and a compatible ZIP is uploaded and has an asset digest. If installation starts and fails, check the manifest ID and version, folder and entry-point names, `supportedOS`, and ZIP contents.
- **Installed add-in has no version list.** Check the owner source, local Fusion registration, manifest ID and GitHub `origin`. Check that the match is unique and that release history contains a stable package for your system. Refresh if the history is still loading or unavailable.
- **Wrong language or missing README.** Check **Display language**, the tag shown in the detail page, exact root filenames, file size, and UTF-8 content. If Russian falls back to English, open the Russian file directly at that tag on GitHub. A newer file on the default branch needs to be included in a subsequent release tag.
- **Missing logo or screenshot.** Check the relative path and case at the tag, image format and size, gallery priority, and the inline image limit. Check English and Russian separately.
