"""Loopback-only TLS browser entrypoint for the PR-05a synthetic experiment."""

import argparse
import base64
import hashlib
import ipaddress
import json
import secrets
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from key_service import KeyService, Rejected

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/cache.mjs": ("cache.mjs", "text/javascript"),
          "/app.mjs": ("app.mjs", "text/javascript"),
          "/lab.css": ("lab.css", "text/css")}
COOKIE = "__Host-pr05a-lab"


def certificate(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PR05a synthetic loopback")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder / "tls.pem", folder / "tls-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert_path, key_path, base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Even synthetic secret-bearing requests are excluded from access logs.

    def reply(self, status, body, *, content_type="application/json", cookie=None):
        data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def token(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        return cookie[COOKIE].value if COOKIE in cookie else None

    def do_GET(self):
        try:
            if self.path in ASSETS:
                filename, mime = ASSETS[self.path]
                self.reply(200, (Path(__file__).parent / filename).read_bytes(), content_type=mime)
            elif self.path == "/lab/bootstrap":
                self.reply(200, {"csrf": self.server.login_csrf})
            elif self.path == "/lab/session":
                self.reply(200, self.server.service.context(self.token()))
            else:
                self.reply(404, {"code": "NOT_FOUND"})
        except Rejected as error:
            self.reply(error.status, {"code": error.code})

    def do_POST(self):
        try:
            if self.headers.get("Host") != self.server.origin.removeprefix("https://") or self.headers.get("Origin") != self.server.origin:
                raise Rejected("ORIGIN_REJECTED")
            if self.headers.get("Content-Type") != "application/json":
                raise Rejected("JSON_REQUIRED", 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise Rejected("BODY_LIMIT", 413)
            def strict_object(pairs):
                obj = dict(pairs)
                if len(obj) != len(pairs):
                    raise ValueError("duplicate JSON member")
                return obj
            body = json.loads(self.rfile.read(length), object_pairs_hook=strict_object)
            if not isinstance(body, dict):
                raise Rejected("INVALID_INPUT", 400)
            csrf = self.headers.get("X-CSRF-Token", "")
            if self.path == "/lab/control":
                control = self.server.control_token
                if not control or not secrets.compare_digest(self.headers.get("X-Pr05a-Control", ""), control):
                    raise Rejected("CONTROL_REJECTED")
                if set(body) != {"action"}:
                    raise Rejected("INVALID_INPUT", 400)
                action = body["action"]
                if action == "password-reset":
                    self.server.service.reset_password("A")
                elif action == "device-revoke":
                    self.server.service.revoke_device("A")
                elif action == "rotate-kek":
                    service = self.server.service
                    old = service.active_kek
                    service.rotate_kek(old + 1, secrets.token_bytes(32))
                    service.retire_kek(old)
                elif action in ("short-window", "normal-window"):
                    self.server.service.window_seconds = 1.2 if action == "short-window" else 30
                else:
                    raise Rejected("INVALID_INPUT", 400)
                self.reply(200, {"applied": True})
                return
            if self.path == "/lab/login":
                if set(body) != {"alias"} or not secrets.compare_digest(csrf, self.server.login_csrf):
                    raise Rejected("CSRF_REJECTED")
                old = self.token()
                if old:
                    self.server.service.logout(old)
                token, context = self.server.service.login(body["alias"])
                self.reply(200, context, cookie=f"{COOKIE}={token}; Secure; HttpOnly; Path=/; SameSite=Strict")
                return
            token = self.token()
            self.server.service.authorize_write(token, csrf)
            if self.path in ("/lab/unlock", "/lab/recover-key"):
                fields = {"scope", "key_epoch"}
                if self.path == "/lab/recover-key":
                    fields.add("key_id")
                if set(body) != fields:
                    raise Rejected("INVALID_INPUT", 400)
                result = self.server.service.unlock(token, body["scope"], body["key_epoch"], recovery_key=body.get("key_id"))
            elif self.path == "/lab/grant" and set(body) == {"grant_id"}:
                result = self.server.service.check_grant(token, body["grant_id"])
            elif self.path == "/lab/confirm-recovery" and not body:
                self.server.service.confirm_recovery(token)
                result = {"confirmed": True}
            elif self.path == "/lab/confirm-recording" and not body:
                self.server.service.confirm_recording(token)
                result = {"confirmed": True}
            elif self.path == "/lab/rotate-dek" and not body:
                self.server.service.rotate_dek(token)
                result = {"rotated": True}
            elif self.path == "/lab/logout" and not body:
                self.server.service.logout(token)
                self.reply(200, {"logged_out": True}, cookie=f"{COOKIE}=; Secure; HttpOnly; Path=/; SameSite=Strict; Max-Age=0")
                return
            else:
                raise Rejected("INVALID_INPUT", 400)
            self.reply(200, result)
        except Rejected as error:
            self.reply(error.status, {"code": error.code})
        except (ValueError, TypeError, KeyError, UnicodeError):
            self.reply(400, {"code": "INVALID_INPUT"})
        except Exception:
            self.reply(503, {"code": "LAB_SERVICE_UNAVAILABLE"})


@contextmanager
def serve(service, folder, *, control_token=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cert, key, pin = certificate(folder)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.service = service
    server.origin = f"https://127.0.0.1:{server.server_port}"
    server.login_csrf = secrets.token_urlsafe(32)
    server.control_token = control_token
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.origin, pin
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    service = KeyService(args.work / "keys.sqlite")
    with serve(service, args.work) as (origin, pin):
        print(json.dumps({"origin": origin, "spki_pin": pin, "mode": "synthetic-only"}), flush=True)
        threading.Event().wait()
