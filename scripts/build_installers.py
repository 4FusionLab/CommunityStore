"""Build native installers that download the latest stable Community Store release."""

import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from tempfile import TemporaryDirectory

if __package__:
    from .build import ROOT
else:
    from build import ROOT


SOURCES = ROOT / 'scripts' / 'installers'
WINDOWS_FILENAME = 'CommunityStore-Windows-Setup.exe'
MAC_FILENAME = 'CommunityStore-macOS.dmg'
MAC_APP_NAME = 'Install Community Store'


def build_downloader(platform, arch, output):
    sources = [SOURCES / 'downloader.go']
    if platform == 'windows':
        sources.append(SOURCES / 'cleanup_windows.go')
    subprocess.run(
        ['go', 'build', '-trimpath', '-buildvcs=false', '-ldflags=-s -w',
         '-o', str(output), *map(str, sources)],
        env={**os.environ, 'GOOS': platform, 'GOARCH': arch, 'CGO_ENABLED': '0'},
        check=True,
    )


def windows_package(work):
    compiler = shutil.which('makensis')
    if not compiler:
        raise RuntimeError('Install NSIS 3 (makensis) to build the Windows setup.')
    build_downloader('windows', 'amd64', work / 'community-store-downloader.exe')
    shutil.copyfile(SOURCES / 'windows.nsi', work / 'windows.nsi')
    for artwork in ('welcome.bmp', 'ready.bmp', 'setup.ico'):
        shutil.copyfile(SOURCES / 'windows' / artwork, work / artwork)
    subprocess.run([compiler, 'windows.nsi'], cwd=work, check=True)
    return (work / 'CommunityStore-Setup.exe').rename(work / WINDOWS_FILENAME)


def macos_package(work, signing_identity):
    if not shutil.which('swiftc') or not shutil.which('hdiutil'):
        raise RuntimeError('Build the macOS installer on a Mac with Xcode Command Line Tools.')
    image_root = work / 'dmg'
    app = image_root / f'{MAC_APP_NAME}.app'
    contents = app / 'Contents'
    binaries = contents / 'MacOS'
    binaries.mkdir(parents=True)
    resources = contents / 'Resources'
    resources.mkdir()
    shutil.copyfile(ROOT / 'CommunityStore' / 'ui' / 'assets' / 'open-catalog.png', resources / 'StoreMark.png')
    shutil.copyfile(SOURCES / 'macos' / 'Setup.icns', resources / 'Setup.icns')
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleDisplayName': MAC_APP_NAME,
        'CFBundleName': MAC_APP_NAME,
        'CFBundleExecutable': 'CommunityStoreInstaller',
        'CFBundleIdentifier': 'com.4fusionlab.communitystore.installer',
        'CFBundleIconFile': 'Setup.icns',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1',
        'CFBundleShortVersionString': '1.0.0',
        'LSMinimumSystemVersion': '12.0',
        'NSHighResolutionCapable': True,
    }))
    for go_arch, apple_arch in [('amd64', 'x86_64'), ('arm64', 'arm64')]:
        build_downloader('darwin', go_arch, work / f'downloader-{apple_arch}')
        subprocess.run([
            'swiftc', '-target', f'{apple_arch}-apple-macosx12.0', '-O',
            str(SOURCES / 'macos' / 'main.swift'), str(SOURCES / 'macos' / 'Recycle.swift'),
            '-o', str(work / f'installer-{apple_arch}'),
        ], check=True)
    for source, target in [('downloader', 'community-store-downloader'), ('installer', 'CommunityStoreInstaller')]:
        subprocess.run([
            'lipo', '-create', str(work / f'{source}-x86_64'), str(work / f'{source}-arm64'),
            '-output', str(binaries / target),
        ], check=True)
    signing = ['codesign', '--force', '--sign', signing_identity or '-']
    if signing_identity:
        signing.extend(['--options', 'runtime', '--timestamp'])
    subprocess.run([*signing, str(binaries / 'community-store-downloader')], check=True)
    subprocess.run([*signing, str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    package = work / MAC_FILENAME
    subprocess.run([
        'hdiutil', 'create', '-volname', MAC_APP_NAME, '-srcfolder', str(image_root),
        '-format', 'UDZO', str(package),
    ], check=True)
    if signing_identity:
        subprocess.run(['codesign', '--sign', signing_identity, '--timestamp', str(package)], check=True)
    return package


def build_installers(platform, output, signing_identity=None):
    if not shutil.which('go'):
        raise RuntimeError('Install Go 1.20 or later to build the release downloader.')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.installer-', dir=output) as temporary:
        work = Path(temporary)
        packages = []
        if platform in ('windows', 'all'):
            packages.append(windows_package(work))
        if platform in ('macos', 'all'):
            packages.append(macos_package(work, signing_identity))
        results = []
        for package in packages:
            checksum = work / f'{package.name}.sha256'
            checksum.write_text(
                f'{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n',
                encoding='ascii', newline='\n',
            )
            results.extend(path.replace(output / path.name) for path in (package, checksum))
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, choices=('windows', 'macos', 'all'))
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'installers')
    parser.add_argument('--mac-signing-identity', help='Developer ID Application identity in the keychain')
    args = parser.parse_args()
    try:
        for path in build_installers(args.platform, args.output, args.mac_signing_identity):
            print(path)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
