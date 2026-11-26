"""Local support logs shared by the bundled loader and staged runtimes."""
import json
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from zipfile import ZIP_DEFLATED, ZipFile

LOG = logging.getLogger("4FusionLab.CommunityStore.diagnostics")
LOG_NAME = "community-store.log"
MAX_BYTES = 512 * 1024
BACKUPS = 3


def log_directory():
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local and Path(local).is_absolute() else Path.home() / "AppData" / "Local"
        return root / "4FusionLab" / "CommunityStore" / "Logs"
    return Path.home() / "Library" / "Logs" / "4FusionLab" / "CommunityStore"


class SupportHandler(RotatingFileHandler):
    failed = False

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return super()._open()

    def emit(self, record):
        self.failed = False
        super().emit(record)

    def handleError(self, record):
        # A full or inaccessible disk must not interfere with Store operations.
        self.failed = True


def start():
    # Both entry points run on Fusion's main thread. Keep one handler across
    # runtime reloads and share its lock with worker threads and ZIP export.
    if LOG.handlers:
        return
    try:
        handler = SupportHandler(log_directory() / LOG_NAME, maxBytes=MAX_BYTES,
                                 backupCount=BACKUPS, encoding="utf-8", delay=True)
    except (OSError, RuntimeError):
        return
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    LOG.propagate = False
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)


def version(value):
    if isinstance(value, str) and re.fullmatch(r"v?[0-9]{1,10}(?:\.[0-9]{1,10}){1,4}", value):
        return value
    return "unknown"


def event(name, *, level=logging.INFO, **fields):
    """Log code-owned labels and explicitly selected metadata."""
    if LOG.handlers:
        LOG.log(level, "%s %s", name, json.dumps(fields, separators=(",", ":")))


def message_key(message):
    key = getattr(message, "key", "")
    return key if isinstance(key, str) and re.fullmatch(r"[a-z_]+\.[a-z_]+", key) else ""


def failure(name, error, **fields):
    """Keep error categories and Store frames without exception text or locals."""
    if not LOG.handlers:
        return
    frames = []
    trace = error.__traceback__
    while trace:
        frame = trace.tb_frame
        module = frame.f_globals.get("__name__", "")
        if module.startswith(("CommunityStore.", "_community_store_")):
            frames.append(f"{module.rsplit('.', 1)[-1]}:{frame.f_code.co_name}:{trace.tb_lineno}")
        trace = trace.tb_next
    fields["error"] = type(error).__name__
    key = message_key(error.args[0]) if error.args else ""
    if key:
        fields["reason"] = key
    if isinstance(error, HTTPError):
        fields["http_status"] = error.code
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, OSError) and type(cause.errno) is int:
        fields["errno"] = cause.errno
    # Windows maps both sharing violations and access denials to EACCES.
    if isinstance(cause, OSError) and type(getattr(cause, "winerror", None)) is int:
        fields["winerror"] = cause.winerror
    if frames:
        fields["frames"] = frames[-12:]
    event(name, level=logging.WARNING, **fields)


def export(destination):
    """Atomically save a snapshot of the current and rotated logs as a ZIP."""
    start()
    event("logs.export_requested")
    if not LOG.handlers:
        raise OSError("Diagnostic log unavailable")
    handler = LOG.handlers[0]
    current = Path(handler.baseFilename)
    snapshot = {}
    handler.acquire()
    try:
        if handler.failed or not current.is_file():
            raise OSError("Diagnostic log unavailable")
        for number in range(handler.backupCount, -1, -1):
            path = current.with_name(f"{current.name}.{number}") if number else current
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    snapshot[path.name] = stream.read(handler.maxBytes)
        if current.name not in snapshot or destination.resolve() in {
            current.with_name(name).resolve() for name in snapshot
        }:
            raise OSError("Invalid diagnostic log destination")
    finally:
        handler.release()

    with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in snapshot.items():
                archive.writestr(name, content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
