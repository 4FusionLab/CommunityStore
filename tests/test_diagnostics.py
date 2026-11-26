import errno
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

from CommunityStore import diagnostics
from CommunityStore.localization import Message


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.logs = self.root / "logs"
        self.destination = self.root / "support.zip"
        self.enterContext(patch.object(diagnostics.LOG, "handlers", []))
        self.enterContext(patch.object(diagnostics, "log_directory", return_value=self.logs))
        self.enterContext(patch.object(diagnostics, "MAX_BYTES", 1024))
        self.addCleanup(self.close_logs)

    def close_logs(self):
        for handler in diagnostics.LOG.handlers:
            handler.close()

    def test_export_includes_recent_rotated_logs_and_supports_worker_writes(self):
        diagnostics.start()
        diagnostics.start()
        self.assertEqual(len(diagnostics.LOG.handlers), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: diagnostics.event("test.worker", item=i), range(180)))
        diagnostics.event("test.latest")
        diagnostics.export(self.destination)

        with ZipFile(self.destination) as archive:
            self.assertEqual(set(archive.namelist()), {
                diagnostics.LOG_NAME, *(f"{diagnostics.LOG_NAME}.{n}" for n in range(1, 4)),
            })
            self.assertTrue(all(item.file_size <= 1024 for item in archive.infolist()))
            history = "".join(archive.read(name).decode() for name in archive.namelist())
            self.assertRegex(history, r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ INFO test\.latest")
            self.assertIn("logs.export_requested", archive.read(diagnostics.LOG_NAME).decode())

    def test_archive_keeps_error_codes_without_request_or_path_details(self):
        diagnostics.start()
        request_url = "https://example.test/?token=mock-secret"
        personal_path = "/Users/example/private CAD design.f3d"
        http_error = HTTPError(request_url, 503, "ABCD-1234", {}, io.BytesIO())
        self.addCleanup(http_error.close)
        sharing_violation = PermissionError(errno.EACCES, "blocked", personal_path)
        sharing_violation.winerror = 32
        for error in (
            http_error,
            sharing_violation,
            ValueError(Message("runtime.folder_used", folder=personal_path)),
        ):
            try:
                raise error
            except Exception as failure:
                diagnostics.failure("test.failure", failure)
        diagnostics.export(self.destination)

        with ZipFile(self.destination) as archive:
            log = archive.read(diagnostics.LOG_NAME).decode()
        for private in (request_url, "mock-secret", "ABCD-1234", personal_path, "blocked"):
            self.assertNotIn(private, log)
        for detail in ('"http_status":503', f'"errno":{errno.EACCES}', '"winerror":32',
                       '"reason":"runtime.folder_used"'):
            self.assertIn(detail, log)

    def test_unwritable_logs_do_not_interrupt_store_or_replace_previous_export(self):
        self.logs.write_text("blocks directory creation", encoding="utf-8")
        self.destination.write_bytes(b"previous export")
        diagnostics.start()
        diagnostics.event("test.logs_unavailable")
        with self.assertRaises(OSError):
            diagnostics.export(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"previous export")

    def test_failed_archive_write_leaves_no_partial_export(self):
        diagnostics.start()
        diagnostics.event("test.before_export")
        self.destination.write_bytes(b"previous export")
        with patch.object(diagnostics, "ZipFile", side_effect=OSError):
            with self.assertRaises(OSError):
                diagnostics.export(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"previous export")
        self.assertEqual(set(self.root.iterdir()), {self.logs, self.destination})
