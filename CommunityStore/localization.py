"""Load the shared catalogs and resolve the operating system's display language."""
import ctypes
import json
import locale
import plistlib
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

LANGUAGES = ("en", "ru")
SYSTEM = "system"
DEFAULT_LANGUAGE = "en"
CATALOGS = Path(__file__).with_name("locales")


@lru_cache(maxsize=1)
def system_language():
    tag = ""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["/usr/bin/defaults", "export", "NSGlobalDomain", "-"],
                capture_output=True, check=True, timeout=2,
            )
            languages = plistlib.loads(result.stdout).get("AppleLanguages") or []
            if languages:
                tag = languages[0]
        elif sys.platform == "win32":
            get_language = ctypes.windll.kernel32.GetUserDefaultUILanguage
            get_language.restype = ctypes.c_ushort
            tag = locale.windows_locale.get(get_language(), "")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    language = tag.lower().replace("_", "-").split("-", 1)[0] if isinstance(tag, str) else ""
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def selected_language(preference=SYSTEM):
    return preference if preference in LANGUAGES else system_language()


@lru_cache(maxsize=len(LANGUAGES))
def catalog(language):
    return json.loads((CATALOGS / (language + ".json")).read_text(encoding="utf-8"))


@lru_cache(maxsize=len(LANGUAGES))
def palette_catalog(language):
    return {key.removeprefix("ui."): text for key, text in catalog(language).items() if key.startswith("ui.")}


def translate(value, language):
    # An initial notice can come from the bundled loader, which runs in a
    # different Python package from a staged runtime.
    render = getattr(value, "for_language", None)
    if render:
        return render(language)
    if isinstance(value, list):
        return [translate(item, language) for item in value]
    if isinstance(value, dict):
        return {key: translate(item, language) for key, item in value.items()}
    return value


class Message(str):
    """Keep a translatable key while remaining compatible with older runtimes."""

    def __new__(cls, key, **values):
        default = catalog(selected_language())[key].format(**values)
        instance = super().__new__(cls, default)
        instance.key = key
        instance.values = values
        return instance

    def for_language(self, language):
        values = {key: translate(value, language) for key, value in self.values.items()}
        return catalog(language)[self.key].format(**values)


def error_message(error):
    if len(error.args) == 1 and hasattr(error.args[0], "for_language"):
        return error.args[0]
    return str(error)
