"""GitHub device authorization for the public catalog."""
import json
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .localization import Message

DEVICE_PAGE = "https://github.com/login/device"
CLIENT_ID = re.compile(r"[A-Za-z0-9_]{1,128}")
TOKEN = re.compile(r"[A-Za-z0-9_]{1,256}")


class AuthorizationError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(path, **fields):
    request = Request(f"https://github.com/login/{path}", data=urlencode(fields).encode("ascii"),
                      headers={"Accept": "application/json", "User-Agent": "Fusion-Community-Store"})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=12)
    except HTTPError as error:
        if error.code != 400:
            error.close()
            raise
        # GitHub returns device_flow_disabled and other OAuth errors in JSON.
        response = error
    with response:
        data = json.loads(response.read(16 * 1024))
    if not isinstance(data, dict):
        raise AuthorizationError(Message("github.failed"))
    return data


def _fail(data):
    error = data.get("error")
    key = {
        "access_denied": "denied",
        "expired_token": "expired",
        "token_expired": "expired",
        "incorrect_client_credentials": "app_invalid",
        "device_flow_disabled": "app_invalid",
    }.get(error if isinstance(error, str) else "", "failed")
    raise AuthorizationError(Message(f"github.{key}"))


def authorize(client_id, cancelled, show_code):
    """Return the token to the caller, or None when the user cancels."""
    if not CLIENT_ID.fullmatch(client_id):
        raise AuthorizationError(Message("github.app_invalid"))
    if cancelled.is_set():
        return None
    try:
        return _authorize(client_id, cancelled, show_code)
    except (OSError, ValueError, HTTPException):
        # Never surface GitHub response bodies or transport details.
        raise AuthorizationError(Message("github.failed")) from None


def _authorize(client_id, cancelled, show_code):
    started = time.monotonic()
    device = _post("device/code", client_id=client_id, scope="")
    code, device_code = device.get("user_code"), device.get("device_code")
    interval, duration = device.get("interval"), device.get("expires_in")
    if not (isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", code)
            and isinstance(device_code, str) and device_code
            and device.get("verification_uri") == DEVICE_PAGE
            and type(interval) is int and interval > 0 and type(duration) is int and duration > 0):
        _fail(device)
    if cancelled.is_set():
        return None
    expires = started + duration
    show_code(code)
    while (remaining := expires - time.monotonic()) > 0:
        if cancelled.wait(min(interval, remaining)):
            return None
        if time.monotonic() >= expires:
            break
        try:
            data = _post("oauth/access_token", client_id=client_id, device_code=device_code,
                         grant_type="urn:ietf:params:oauth:grant-type:device_code")
        except (URLError, TimeoutError) as error:
            # Back off after connection timeouts. HTTP errors without an OAuth
            # response end the attempt rather than driving repeated requests.
            if getattr(error, "code", None):
                raise
            interval *= 2
            continue
        if cancelled.is_set():
            return None
        token = data.get("access_token")
        if isinstance(token, str) and TOKEN.fullmatch(token):
            if data.get("scope") != "" or str(data.get("token_type", "")).lower() != "bearer":
                raise AuthorizationError(Message("github.scope_invalid"))
            return token
        error = data.get("error")
        if error == "slow_down":
            updated = data.get("interval")
            interval = max(interval + 5, updated) if type(updated) is int else interval + 5
        elif error != "authorization_pending":
            _fail(data)
    raise AuthorizationError(Message("github.expired"))
