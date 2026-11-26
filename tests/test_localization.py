"""Verify locale selection and the catalogs shipped with the palette."""
import plistlib
import re
import unittest
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock, patch

from CommunityStore import localization as i18n

ROOT = Path(__file__).resolve().parents[1] / "CommunityStore"


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        i18n.system_language.cache_clear()
        self.addCleanup(i18n.system_language.cache_clear)

    def test_mac_uses_display_language_and_defaults_to_english(self):
        for display, expected in (("ru-RU", "ru"), ("en-GB", "en"), ("de-DE", "en")):
            with self.subTest(display=display):
                i18n.system_language.cache_clear()
                preferences = plistlib.dumps({"AppleLanguages": [display, "ru"], "AppleLocale": "ru_RU"})
                with (patch.object(i18n.sys, "platform", "darwin"),
                      patch.object(i18n.subprocess, "run", return_value=SimpleNamespace(stdout=preferences))):
                    self.assertEqual(i18n.selected_language(), expected)
                    self.assertEqual(i18n.selected_language("system"), expected)
                    self.assertEqual(i18n.selected_language("en"), "en")
                    self.assertEqual(i18n.selected_language("ru"), "ru")

    def test_windows_uses_current_users_ui_language(self):
        get_language = Mock(return_value=0x0419)
        win32 = SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=get_language))
        with (patch.object(i18n.sys, "platform", "win32"),
              patch.object(i18n.ctypes, "windll", win32, create=True)):
            self.assertEqual(i18n.selected_language(), "ru")
        get_language.assert_called_once_with()

    def test_missing_system_setting_falls_back_to_english(self):
        with (patch.object(i18n.sys, "platform", "darwin"),
              patch.object(i18n.subprocess, "run", side_effect=OSError)):
            self.assertEqual(i18n.selected_language(), "en")

    def test_catalog_keys_and_placeholders_match(self):
        english, russian = (i18n.catalog(language) for language in i18n.LANGUAGES)
        self.assertEqual(english.keys(), russian.keys())
        formatter = Formatter()
        for key, text in english.items():
            with self.subTest(key=key):
                fields_en = {field for _, field, _, _ in formatter.parse(text) if field}
                fields_ru = {field for _, field, _, _ in formatter.parse(russian[key]) if field}
                self.assertEqual(fields_en, fields_ru)
        palette = i18n.palette_catalog("en")
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        js = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "ui").glob("*.js"))
        keys = set(re.findall(r'data-i18n(?:-[\w-]+)?="(\w+)"', html))
        keys.update(re.findall(r'\bt\("(\w+)"(?=[,)])', js))
        self.assertLessEqual(keys, palette.keys())

    def test_removal_choices_have_localized_labels_titles_and_consequences(self):
        for language in i18n.LANGUAGES:
            words = i18n.palette_catalog(language)
            choices = ("remove", "unlink", "hide_addin")
            with self.subTest(language=language):
                self.assertEqual(len({words[choice] for choice in choices}), len(choices))
                for choice in choices:
                    self.assertIn("{name}", words[f"{choice}_confirm"])
                    self.assertTrue(words[f"{choice}_description"])

    def test_messages_already_produced_can_be_rendered_in_another_language(self):
        message = i18n.Message("runtime.rollback_failed", reason=i18n.Message("runtime.start_unconfirmed"),
                               error="example", backup="/tmp")
        original = ValueError(message)
        self.assertIs(i18n.error_message(original), message)
        self.assertIn("Fusion did not confirm", i18n.translate(message, "en"))
        self.assertIn("Fusion не подтвердил", i18n.translate(message, "ru"))
        self.assertIn("example", i18n.translate(message, "ru"))
