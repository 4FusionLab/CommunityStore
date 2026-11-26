# Test Community Store in Fusion for Windows

[Русская версия](WINDOWS_COMMUNITY.ru.md)

Use these scenarios to test the release ZIP in Fusion for Windows x64. Installation requires Fusion, the Community Store ZIP and its checksum file. Fusion provides Python. Use test add-ins for installation, updates and removal.

## Install

1. Download the ZIP from the [1.0.0 release](https://github.com/4FusionLab/CommunityStore/releases/tag/v1.0.0) and save it with its matching `.zip.sha256` file in one folder. Open PowerShell there and run the commands below. Obtain a fresh copy if the hashes differ.
2. In File Explorer, choose **Extract All**. Extract into a permanent folder in your user profile where you can write files. A path with spaces and non-Latin characters is suitable. The inner `CommunityStore` folder should contain both `CommunityStore.py` and `CommunityStore.manifest`.
3. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins**. Add the folder containing the manifest, select **Run**, and enable **Run on Startup**. Open **Community Store** in the Design workspace's **Add-Ins** panel.

```powershell
$zip = ".\CommunityStore-1.0.0.zip"
$expected = ((Get-Content "$zip.sha256" -Raw).Trim() -split '\s+')[0]
(Get-FileHash $zip -Algorithm SHA256).Hash -eq $expected
```

PowerShell should print `True`.

## What to check

| Scenario | Expected result |
|---|---|
| First run without GitHub | The guide appears. After completing it, search Marketplace, open an add-in and install a compatible release while signed out. |
| Custom sources | Add a public GitHub organization and user with add-in releases in **Sources**. Examples are `ACDC4Robot` and `906grown`. Their cards appear. Install a test add-in, remove its source and check that the add-in remains in **My Add-Ins**. |
| Windows packages and updates | For a release with several ZIPs, check that you can choose the Windows package. Install ACDC4Robot from Marketplace. In **My Add-Ins**, set its target to v1.1.0 and apply the change if required. Once the installed version reads 1.1.0, set the target to v1.1.1 and apply the update. Verify its version, running and startup settings and the backup of the previous managed folder. Use **Update selected** with two test add-ins and compare the preview with the per-item results. |
| Store restart and self-update | Close and reopen Fusion with Run on Startup enabled. The registered Store opens with the same settings. If Store offers a newer published version, stage it, restart as prompted, and record both versions. If no newer version is available, mark the self-update step as pending. |
| Locked file | On a managed test installation, try an update with a file in the old folder held open exclusively. If Windows rejects the operation, the previous files should survive and Store should report the outcome. Release the lock and retry. Record whether Windows actually refused the operation. |
| Local folder and management | Link a test add-in from a path with spaces and non-Latin characters. Check start, stop, opening the folder in File Explorer and Run on startup. Unlink it and check its original files. For an archived add-in, check **Restore from archive → Open archive**. |
| Sign-in and restart | In **Settings**, sign in to GitHub, copy the code with the button and confirm in the browser. Restart Fusion. Store should start once and retain settings, sources and the GitHub profile. Sign out and check that the anonymous catalog still works. |
| Palette and diagnostics | Check EN/RU, light/dark appearance, a narrow palette, Tab, Enter and Escape. After changing appearance, also check a freshly opened palette. **Settings → Export logs…** should open a file picker and save a ZIP. |

To reproduce the locked-file case, use a disposable ACDC4Robot v1.1.0 installation. Open PowerShell in the installed add-in folder. Keep the window open while you try the update in Fusion.

```powershell
$folder = (Get-Item -LiteralPath ".\ACDC4Robot.manifest").DirectoryName
$probe = Join-Path $folder ("lock-test-" + [guid]::NewGuid() + ".tmp")
$lock = [System.IO.File]::Open($probe, 'CreateNew', 'ReadWrite', 'None')
```

Run `$lock.Dispose()` in the same window, then retry the update. On Windows, renaming a parent directory can still succeed with some file handles. Report the outcome you actually see.

## Report results

Include your Windows version and build, architecture, Fusion version, Store SHA-256, test add-in source/tag and selected ZIP name. For a defect, include steps, expected and actual results, error text and a screenshot. For a failed update, include versions, running states and whether a backup exists. If the palette does not open, logs are in `%LOCALAPPDATA%\4FusionLab\CommunityStore\Logs`. Remove GitHub codes and personal information from attachments before sharing. [Community Store issues](https://github.com/4FusionLab/CommunityStore/issues)
