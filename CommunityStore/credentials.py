"""Per-user GitHub credentials backed by the operating system vault."""
import ctypes as c
import sys
from ctypes import wintypes as w

SERVICE = "com.4fusionlab.communitystore.github"


class CredentialError(Exception):
    def __init__(self):
        super().__init__("GitHub credential store unavailable")


def open_store(client_id):
    try:
        if sys.platform == "darwin":
            return MacKeychain(client_id)
        if sys.platform == "win32":
            return WindowsCredentials(client_id)
    except (OSError, AttributeError):
        raise CredentialError from None
    raise CredentialError


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class MacKeychain:
    NOT_FOUND = -25300
    DUPLICATE = -25299

    def __init__(self, account):
        security = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
        foundation = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer, length, status = c.c_void_p, c.c_uint32, c.c_int32
        key = (pointer, length, c.c_char_p, length, c.c_char_p)
        self.find = _bind(security, "SecKeychainFindGenericPassword", status, *key,
                          c.POINTER(length), c.POINTER(pointer), c.POINTER(pointer))
        self.add = _bind(security, "SecKeychainAddGenericPassword", status, *key, length, pointer,
                         c.POINTER(pointer))
        self.modify = _bind(security, "SecKeychainItemModifyAttributesAndData", status,
                            pointer, pointer, length, pointer)
        self.remove = _bind(security, "SecKeychainItemDelete", status, pointer)
        self.free_data = _bind(security, "SecKeychainItemFreeContent", status, pointer, pointer)
        self.release = _bind(foundation, "CFRelease", None, pointer)
        service, account = SERVICE.encode("ascii"), account.encode("ascii")
        self.key = (None, len(service), service, len(account), account)

    def load(self):
        size, data = c.c_uint32(), c.c_void_p()
        status = self.find(*self.key, c.byref(size), c.byref(data), None)
        if status == self.NOT_FOUND:
            return None
        if status:
            raise CredentialError
        try:
            return c.string_at(data, size.value).decode("ascii")
        except UnicodeError:
            raise CredentialError from None
        finally:
            self.free_data(None, data)

    def save(self, token):
        secret = token.encode("ascii")
        status = self.add(*self.key, len(secret), secret, None)
        if status == self.DUPLICATE:
            item = c.c_void_p()
            if self.find(*self.key, None, None, c.byref(item)):
                raise CredentialError
            try:
                status = self.modify(item, None, len(secret), secret)
            finally:
                self.release(item)
        if status:
            raise CredentialError

    def delete(self):
        item = c.c_void_p()
        status = self.find(*self.key, None, None, c.byref(item))
        if status == self.NOT_FOUND:
            return
        if status:
            raise CredentialError
        try:
            if self.remove(item) not in (0, self.NOT_FOUND):
                raise CredentialError
        finally:
            self.release(item)


class WindowsCredential(c.Structure):
    _fields_ = [
        ("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
        ("Comment", w.LPWSTR), ("LastWritten", w.FILETIME),
        ("CredentialBlobSize", w.DWORD), ("CredentialBlob", c.POINTER(w.BYTE)),
        ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", c.c_void_p),
        ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR),
    ]


class WindowsCredentials:
    GENERIC = 1
    LOCAL_MACHINE = 2  # Persists for this user on this machine.
    NOT_FOUND = 1168

    def __init__(self, account):
        api = c.WinDLL("Advapi32.dll", use_last_error=True)
        pointer = c.POINTER(WindowsCredential)
        self.read = _bind(api, "CredReadW", w.BOOL, w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(pointer))
        self.write = _bind(api, "CredWriteW", w.BOOL, pointer, w.DWORD)
        self.remove = _bind(api, "CredDeleteW", w.BOOL, w.LPCWSTR, w.DWORD, w.DWORD)
        self.free = _bind(api, "CredFree", None, c.c_void_p)
        self.target = f"{SERVICE}/{account}"

    def load(self):
        item = c.POINTER(WindowsCredential)()
        if not self.read(self.target, self.GENERIC, 0, c.byref(item)):
            if c.get_last_error() == self.NOT_FOUND:
                return None
            raise CredentialError
        try:
            credential = item.contents
            return c.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("ascii")
        except UnicodeError:
            raise CredentialError from None
        finally:
            self.free(item)

    def save(self, token):
        secret = token.encode("ascii")
        buffer = (w.BYTE * len(secret)).from_buffer_copy(secret)
        credential = WindowsCredential(Type=self.GENERIC, TargetName=self.target, UserName="GitHub",
                                       CredentialBlobSize=len(secret), CredentialBlob=buffer,
                                       Persist=self.LOCAL_MACHINE)
        try:
            if not self.write(c.byref(credential), 0):
                raise CredentialError
        finally:
            c.memset(buffer, 0, len(secret))

    def delete(self):
        if not self.remove(self.target, self.GENERIC, 0) and c.get_last_error() != self.NOT_FOUND:
            raise CredentialError
