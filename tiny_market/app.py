from __future__ import annotations

import logging
import mimetypes
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote

from .config import Config
from .db import connect, initialize, transaction
from .security import (
    NICKNAME_RE,
    USERNAME_RE,
    constant_time_equal,
    hash_password,
    new_csrf_token,
    new_session_token,
    parse_cookies,
    privacy_hash,
    read_form_data,
    session_expiry,
    token_hash,
    validate_image,
    validate_password,
    verify_password,
)
from . import views


LOGGER = logging.getLogger("tiny_market")
STATIC_DIR = Path(__file__).with_name("static")
FAKE_PASSWORD_HASH = "pbkdf2_sha256$600000$00000000000000000000000000000000$935bd817209109b987ada4e00cb538f0c9e323a716b633f24f186186e86ad6d8"


class Response:
    def __init__(self, body: str | bytes = b"", status: int = 200, headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = list(headers or [])


class TinyMarketApp:
    def __init__(self, config: Config):
        self.config = config
        self.upload_dir = config.database_path.parent / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        initialize(config.database_path)

    def __call__(self, environ: dict, start_response: Callable):
        response: Response
        try:
            response = self.dispatch(environ)
        except ValueError as error:
            response = self.page_response(views.message_page("잘못된 요청", str(error), status=400), 400)
        except Exception:
            LOGGER.exception("Unhandled request failure")
            message = "잠시 후 다시 시도해 주세요. 문제가 계속되면 관리자에게 문의해 주세요."
            response = self.page_response(views.message_page("서버 오류", message, status=500), 500)

        self.add_security_headers(response)
        status_text = {
            200: "200 OK", 201: "201 Created", 302: "302 Found", 400: "400 Bad Request",
            401: "401 Unauthorized", 403: "403 Forbidden", 404: "404 Not Found",
            405: "405 Method Not Allowed", 409: "409 Conflict", 413: "413 Content Too Large",
            429: "429 Too Many Requests", 500: "500 Internal Server Error",
        }.get(response.status, f"{response.status} Unknown")
        response.headers.append(("Content-Length", str(len(response.body))))
        start_response(status_text, response.headers)
        return [response.body]

    def dispatch(self, environ: dict) -> Response:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = unquote(environ.get("PATH_INFO", "/"))
        if "\x00" in path or ".." in path:
            return self.error(400, "잘못된 경로", "요청한 경로를 처리할 수 없습니다.")
        if path == "/health" and method in {"GET", "HEAD"}:
            return Response(b"" if method == "HEAD" else b"ok", 200, [("Content-Type", "text/plain; charset=utf-8")])
        if path.startswith("/static/"):
            return self.serve_static(method, path)
        if path.startswith("/uploads/"):
            return self.serve_upload(method, path)

        session, user, cookie_header = self.load_or_create_session(environ)
        csrf_token = session["csrf_token"]
        if path.startswith("/chat-uploads/"):
            return self.with_cookie(self.serve_chat_upload(method, path, user, csrf_token), cookie_header)
        if method == "POST":
            content_type = environ.get("CONTENT_TYPE", "").lower()
            multipart = content_type.startswith("multipart/form-data")
            upload_path = path in {"/products/new", "/chat/send"} or bool(re.fullmatch(r"/products/[1-9][0-9]*/edit", path))
            if multipart and not upload_path:
                return self.with_cookie(self.error(400, "잘못된 업로드 요청", "이 주소에서는 파일을 받을 수 없습니다.", user, csrf_token), cookie_header)
            if multipart:
                denied = self.require_active(user, csrf_token)
                if denied:
                    return self.with_cookie(denied, cookie_header)
            form, files = read_form_data(environ, max_bytes=(32 * 1024 * 1024 if multipart else 128 * 1024))
            if not constant_time_equal(form.get("csrf_token", ""), csrf_token):
                return self.with_cookie(self.error(403, "요청을 확인할 수 없습니다", "페이지를 새로고침한 뒤 다시 시도해 주세요.", user, csrf_token), cookie_header)
        else:
            form = {}
            files = {}

        if path == "/admin" or path.startswith("/admin/"):
            denied = self.require_admin_session(session, user, csrf_token)
            if denied:
                return self.with_cookie(denied, cookie_header)

        response: Response
        if path == "/" and method == "GET":
            response = self.home(environ, user, csrf_token)
        elif path == "/api/unread" and method == "GET":
            response = Response(f'{{"unread":{int(user.get("unread_count", 0)) if user else 0}}}', 200, [("Content-Type", "application/json; charset=utf-8")])
        elif path == "/register" and method == "GET":
            response = self.page_response(views.auth_page("register", csrf_token=csrf_token))
        elif path == "/register" and method == "POST":
            response = self.register(form, environ, session)
        elif path == "/login" and method == "GET":
            response = self.page_response(views.auth_page("login", csrf_token=csrf_token))
        elif path == "/login" and method == "POST":
            response = self.login(form, environ, session)
        elif path == "/logout" and method == "POST":
            response = self.logout(environ, session, user)
        elif path == "/products/new" and method == "GET":
            response = self.require_active(user, csrf_token) or self.page_response(views.product_form(csrf_token=csrf_token, user=user))
        elif path == "/products/new" and method == "POST":
            response = self.require_active(user, csrf_token) or self.create_product(form, files, environ, user, csrf_token)
        elif path == "/my" and method == "GET":
            response = self.require_login(user, csrf_token) or self.my_page(user, csrf_token)
        elif path == "/profile/edit" and method == "GET":
            response = self.require_login(user, csrf_token) or self.page_response(views.profile_edit(user=user, csrf_token=csrf_token))
        elif path == "/profile/edit" and method == "POST":
            response = self.require_login(user, csrf_token) or self.update_profile(form, environ, user, csrf_token)
        elif path == "/account/withdraw" and method == "GET":
            response = self.require_login(user, csrf_token) or self.withdraw_page(user, csrf_token)
        elif path == "/account/withdraw" and method == "POST":
            response = self.require_login(user, csrf_token) or self.withdraw_account(form, environ, session, user, csrf_token)
        elif path == "/chat" and method == "GET":
            response = self.require_login(user, csrf_token) or self.chat_inbox(user, csrf_token)
        elif path == "/chat/send" and method == "POST":
            response = self.require_active(user, csrf_token) or self.send_message(form, files, environ, user, csrf_token)
        elif path == "/admin" and method == "GET":
            response = self.require_admin(user, csrf_token) or self.admin_page(user, csrf_token)
        else:
            match = re.fullmatch(r"/products/([1-9][0-9]*)(?:/(edit|delete|checkout|purchase))?", path)
            user_match = re.fullmatch(r"/users/([1-9][0-9]*)", path)
            chat_match = re.fullmatch(r"/chat/([1-9][0-9]*)(?:/([1-9][0-9]*))?", path)
            chat_report_match = re.fullmatch(r"/report/chat/([1-9][0-9]*)/([1-9][0-9]*)", path)
            report_match = re.fullmatch(r"/report/(user|product)/([1-9][0-9]*)", path)
            block_match = re.fullmatch(r"/block/([1-9][0-9]*)/(toggle)", path)
            admin_match = re.fullmatch(r"/admin/(user|product)/([1-9][0-9]*)/(toggle|delete)", path)
            admin_report_match = re.fullmatch(r"/admin/report/([1-9][0-9]*)/(resolve|dismiss|reopen)", path)
            admin_block_match = re.fullmatch(r"/admin/block/([1-9][0-9]*)/delete", path)
            if match:
                response = self.product_route(int(match.group(1)), match.group(2), method, form, files, environ, user, csrf_token)
            elif user_match and method == "GET":
                response = self.user_profile(int(user_match.group(1)), user, csrf_token)
            elif chat_match and method == "GET":
                response = self.require_login(user, csrf_token) or self.chat_page(user, csrf_token, int(chat_match.group(1)), int(chat_match.group(2)) if chat_match.group(2) else None)
            elif chat_report_match and method in {"GET", "POST"}:
                response = self.require_login(user, csrf_token) or self.chat_report(
                    int(chat_report_match.group(1)), int(chat_report_match.group(2)),
                    method, form, environ, user, csrf_token,
                )
            elif report_match and method in {"GET", "POST"}:
                response = self.require_login(user, csrf_token) or self.report(report_match.group(1), int(report_match.group(2)), method, form, environ, user, csrf_token)
            elif block_match and method == "POST":
                response = self.require_login(user, csrf_token) or self.toggle_block(int(block_match.group(1)), environ, user, csrf_token)
            elif admin_match and method == "POST":
                response = self.require_admin(user, csrf_token) or self.admin_action(admin_match.group(1), int(admin_match.group(2)), admin_match.group(3), environ, user, csrf_token)
            elif admin_report_match and method == "POST":
                response = self.require_admin(user, csrf_token) or self.admin_report_action(int(admin_report_match.group(1)), admin_report_match.group(2), environ, user, csrf_token)
            elif admin_block_match and method == "POST":
                response = self.require_admin(user, csrf_token) or self.admin_remove_block(int(admin_block_match.group(1)), environ, user, csrf_token)
            else:
                response = self.error(404, "페이지를 찾을 수 없습니다", "주소를 다시 확인해 주세요.", user, csrf_token)

        return self.with_cookie(response, cookie_header)

    def add_security_headers(self, response: Response) -> None:
        if not any(key.lower() == "content-type" for key, _ in response.headers):
            response.headers.append(("Content-Type", "text/html; charset=utf-8"))
        response.headers.extend([
            ("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Cache-Control", "no-store"),
        ])
        if self.config.secure_cookie:
            response.headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))

    def load_or_create_session(self, environ: dict):
        now = int(time.time())
        cookies = parse_cookies(environ)
        supplied = cookies.get("tiny_session")
        supplied_token = supplied.value if supplied else ""
        with connect(self.config.database_path) as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            session = None
            if supplied_token:
                session = connection.execute(
                    "SELECT s.*, u.username, u.nickname, u.bio, u.balance, u.role, u.status user_status,u.deleted_at FROM sessions s LEFT JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at >= ?",
                    (token_hash(supplied_token), now),
                ).fetchone()
            if session:
                user = ({"id": session["user_id"], "username": session["username"], "nickname": session["nickname"], "bio": session["bio"], "balance": session["balance"], "role": session["role"], "status": session["user_status"], "deleted_at": session["deleted_at"]} if session["user_id"] and session["deleted_at"] is None else None)
                if user:
                    user["unread_count"] = connection.execute(
                        "SELECT COUNT(*) FROM messages m JOIN products p ON p.id=m.product_id WHERE m.recipient_id=? AND m.read_at IS NULL",
                        (user["id"],),
                    ).fetchone()[0]
                return session, user, None
            raw_token = new_session_token()
            csrf = new_csrf_token()
            connection.execute(
                "INSERT INTO sessions(token_hash, user_id, csrf_token, expires_at) VALUES (?, NULL, ?, ?)",
                (token_hash(raw_token), csrf, session_expiry()),
            )
            session = {"token_hash": token_hash(raw_token), "user_id": None, "csrf_token": csrf}
            return session, None, self.cookie_value(raw_token)

    def rotate_session(self, old_token_hash: str, user_id: int) -> str:
        raw_token = new_session_token()
        with transaction(self.config.database_path) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (old_token_hash,))
            connection.execute(
                "INSERT INTO sessions(token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash(raw_token), user_id, new_csrf_token(), session_expiry()),
            )
        return self.cookie_value(raw_token)

    def cookie_value(self, raw_token: str, *, delete: bool = False) -> str:
        value = f"tiny_session={raw_token}; Path=/; HttpOnly; SameSite=Strict"
        if self.config.secure_cookie:
            value += "; Secure"
        if delete:
            value += "; Max-Age=0"
        else:
            value += f"; Max-Age={8 * 60 * 60}"
        return value

    @staticmethod
    def with_cookie(response: Response, cookie_header: str | None) -> Response:
        if cookie_header and not any(key.lower() == "set-cookie" for key, _ in response.headers):
            response.headers.append(("Set-Cookie", cookie_header))
        return response

    @staticmethod
    def page_response(body: str, status: int = 200) -> Response:
        return Response(body, status, [("Content-Type", "text/html; charset=utf-8")])

    @staticmethod
    def redirect(location: str, cookie_header: str | None = None) -> Response:
        response = Response(b"", 302, [("Location", location)])
        return TinyMarketApp.with_cookie(response, cookie_header)

    def error(self, status: int, title: str, message: str, user=None, csrf_token: str = "") -> Response:
        return self.page_response(views.message_page(title, message, status=status, user=user, csrf_token=csrf_token), status)

    def require_login(self, user, csrf_token: str):
        if not user:
            return self.error(401, "로그인이 필요합니다", "이 기능을 사용하려면 먼저 로그인해 주세요.", None, csrf_token)
        return None

    def require_active(self, user, csrf_token: str):
        denied = self.require_login(user, csrf_token)
        if denied:
            return denied
        if user.get("status") != "active":
            return self.error(403, "활동이 제한된 계정입니다", "계정에 로그인해 내역을 확인할 수 있지만 판매·구매·메시지 전송은 할 수 없습니다.", user, csrf_token)
        return None

    def require_admin(self, user, csrf_token: str):
        denied = self.require_login(user, csrf_token)
        if denied:
            return denied
        if user.get("role") != "admin":
            return self.error(403, "권한이 없습니다", "관리자만 접근할 수 있습니다.", user, csrf_token)
        return None

    def require_admin_session(self, session, user, csrf_token: str):
        denied = self.require_admin(user, csrf_token)
        if denied:
            return denied
        with connect(self.config.database_path) as connection:
            authorized = connection.execute(
                """SELECT 1
                   FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>=?
                     AND u.role='admin'
                   LIMIT 1""",
                (session["token_hash"], user["id"], int(time.time())),
            ).fetchone()
        if not authorized:
            return self.error(
                403, "관리자 세션을 확인할 수 없습니다",
                "다시 로그인한 뒤 관리자 페이지를 이용해 주세요.", user, csrf_token,
            )
        return None

    def serve_static(self, method: str, path: str) -> Response:
        if method != "GET":
            return self.error(405, "허용되지 않는 요청", "GET 요청만 사용할 수 있습니다.")
        name = path.removeprefix("/static/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            return self.error(404, "파일을 찾을 수 없습니다", "요청한 파일이 없습니다.")
        target = STATIC_DIR / name
        if not target.is_file():
            return self.error(404, "파일을 찾을 수 없습니다", "요청한 파일이 없습니다.")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(target.read_bytes(), 200, [("Content-Type", content_type), ("Cache-Control", "public, max-age=3600")])

    def serve_upload(self, method: str, path: str) -> Response:
        if method != "GET":
            return self.error(405, "허용되지 않는 요청", "GET 요청만 사용할 수 있습니다.")
        name = path.removeprefix("/uploads/")
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png)", name):
            return self.error(404, "사진을 찾을 수 없습니다", "요청한 사진이 없습니다.")
        target = self.upload_dir / name
        with connect(self.config.database_path) as connection:
            public_image = connection.execute(
                """SELECT 1 FROM product_images pi
                   JOIN products p ON p.id=pi.product_id JOIN users u ON u.id=p.seller_id
                   WHERE pi.filename=? AND p.moderation_status='visible'
                     AND u.status='active' AND u.deleted_at IS NULL
                   UNION
                   SELECT 1 FROM products p JOIN users u ON u.id=p.seller_id
                   WHERE p.image_filename=? AND p.moderation_status='visible'
                     AND u.status='active' AND u.deleted_at IS NULL
                   LIMIT 1""",
                (name, name),
            ).fetchone()
        if not public_image or not target.is_file():
            return self.error(404, "사진을 찾을 수 없습니다", "요청한 사진이 없습니다.")
        content_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return Response(target.read_bytes(), 200, [("Content-Type", content_type), ("Cache-Control", "public, max-age=86400")])

    def serve_chat_upload(self, method: str, path: str, user, csrf_token: str) -> Response:
        if method != "GET" or not user:
            return self.error(404, "사진을 찾을 수 없습니다", "요청한 사진이 없습니다.", user, csrf_token)
        name = path.removeprefix("/chat-uploads/")
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png)", name):
            return self.error(404, "사진을 찾을 수 없습니다", "요청한 사진이 없습니다.", user, csrf_token)
        with connect(self.config.database_path) as connection:
            allowed = connection.execute(
                """SELECT 1 FROM message_images mi JOIN messages m ON m.id=mi.message_id
                   WHERE mi.filename=? AND (m.sender_id=? OR m.recipient_id=?)
                   AND NOT EXISTS (SELECT 1 FROM user_blocks b WHERE
                     (b.blocker_id=m.sender_id AND b.blocked_id=m.recipient_id) OR
                     (b.blocker_id=m.recipient_id AND b.blocked_id=m.sender_id)) LIMIT 1""",
                (name, user["id"], user["id"]),
            ).fetchone()
        target = self.upload_dir / name
        if not allowed or not target.is_file():
            return self.error(404, "사진을 찾을 수 없습니다", "요청한 사진이 없습니다.", user, csrf_token)
        content_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return Response(target.read_bytes(), 200, [("Content-Type", content_type), ("Cache-Control", "private, no-store")])

    def home(self, environ: dict, user, csrf_token: str) -> Response:
        query_params = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=5)
        query = query_params.get("q", [""])[-1].strip()[:80]
        category = query_params.get("category", [""])[-1].strip()
        if category and category not in views.CATEGORIES:
            return self.error(400, "올바르지 않은 상품 분류입니다", "제공되는 상품 분류만 선택해 주세요.", user, csrf_token)
        with connect(self.config.database_path) as connection:
            block_sql = ""
            category_sql = ""
            params: list[object] = []
            if user:
                block_sql = " AND NOT EXISTS (SELECT 1 FROM user_blocks b WHERE (b.blocker_id=? AND b.blocked_id=p.seller_id) OR (b.blocker_id=p.seller_id AND b.blocked_id=?))"
                params.extend([user["id"], user["id"]])
            if category:
                category_sql = " AND p.category=?"
                params.append(category)
            if query:
                # Match when at least one meaningful character from the query
                # appears in the product title. Deduplication and a hard cap
                # keep the generated, fully parameterized SQL bounded.
                characters = list(dict.fromkeys(character for character in query if character.isalnum()))[:32]
                if characters:
                    character_sql = " OR ".join("p.title LIKE ?" for _ in characters)
                    products = connection.execute(
                        "SELECT p.*, u.nickname seller_name FROM products p JOIN users u ON u.id=p.seller_id WHERE p.moderation_status='visible' AND u.status='active'"
                        + block_sql
                        + category_sql
                        + f" AND ({character_sql}) ORDER BY p.created_at DESC, p.id DESC LIMIT 100",
                        (*params, *(f"%{character}%" for character in characters)),
                    ).fetchall()
                else:
                    products = []
            else:
                products = connection.execute(
                    "SELECT p.*, u.nickname seller_name FROM products p JOIN users u ON u.id=p.seller_id WHERE p.moderation_status='visible' AND u.status='active'" + block_sql + category_sql + " ORDER BY p.created_at DESC, p.id DESC LIMIT 100",
                    params,
                ).fetchall()
        return self.page_response(views.home(products, query=query, category=category, user=user, csrf_token=csrf_token))

    def register(self, form: dict, environ: dict, session) -> Response:
        username = form.get("username", "").strip()
        nickname = form.get("nickname", "").strip()
        password = form.get("password", "")
        issues = []
        if not USERNAME_RE.fullmatch(username):
            issues.append("아이디는 3~24자의 영문, 숫자, 밑줄만 사용할 수 있습니다.")
        if not NICKNAME_RE.fullmatch(nickname):
            issues.append("닉네임은 2~20자의 한글, 영문, 숫자, 밑줄만 사용할 수 있습니다.")
        issues.extend(validate_password(password))
        if issues:
            return self.page_response(views.auth_page("register", form={"username": username, "nickname": nickname}, error_items=issues, csrf_token=session["csrf_token"]), 400)
        try:
            with transaction(self.config.database_path) as connection:
                cursor = connection.execute("INSERT INTO users(username, nickname, password_hash) VALUES (?, ?, ?)", (username, nickname, hash_password(password)))
                user_id = cursor.lastrowid
                self.audit(connection, user_id, "user.register", "user", user_id, environ)
        except sqlite3.IntegrityError:
            return self.page_response(views.auth_page("register", form={"username": username, "nickname": nickname}, error_items=["이미 사용 중인 아이디 또는 닉네임입니다."], csrf_token=session["csrf_token"]), 409)
        return self.redirect("/", self.rotate_session(session["token_hash"], user_id))

    def login(self, form: dict, environ: dict, session) -> Response:
        username = form.get("username", "").strip()
        password = form.get("password", "")
        remote_address = environ.get("REMOTE_ADDR", "")
        identity = privacy_hash(f"login-account:{remote_address}:{username.lower()}")
        ip_identity = privacy_hash(f"login-ip:{remote_address}")
        cutoff = int(time.time()) - 15 * 60
        with connect(self.config.database_path) as connection:
            failures = connection.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE identity_hash=? AND attempted_at>=? AND successful=0",
                (identity, cutoff),
            ).fetchone()[0]
            ip_failures = connection.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE identity_hash=? AND attempted_at>=? AND successful=0",
                (ip_identity, cutoff),
            ).fetchone()[0]
            user = connection.execute("SELECT id,username,password_hash,status,deleted_at FROM users WHERE username=?", (username,)).fetchone()
        if failures >= 5 or ip_failures >= 20:
            return self.page_response(views.auth_page("login", form={"username": username}, error_items=["로그인 시도가 너무 많습니다. 15분 후 다시 시도해 주세요."], csrf_token=session["csrf_token"]), 429)
        password_valid = verify_password(password, user["password_hash"] if user else FAKE_PASSWORD_HASH)
        valid = bool(user) and user["deleted_at"] is None and password_valid
        with transaction(self.config.database_path) as connection:
            connection.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (int(time.time()) - 86400,))
            connection.execute("INSERT INTO login_attempts(identity_hash, attempted_at, successful) VALUES (?, ?, ?)", (identity, int(time.time()), int(valid)))
            connection.execute("INSERT INTO login_attempts(identity_hash, attempted_at, successful) VALUES (?, ?, ?)", (ip_identity, int(time.time()), int(valid)))
            if valid:
                self.audit(connection, user["id"], "user.login", "user", user["id"], environ)
        if not valid:
            return self.page_response(views.auth_page("login", form={"username": username}, error_items=["아이디 또는 비밀번호가 올바르지 않습니다."], csrf_token=session["csrf_token"]), 401)
        return self.redirect("/", self.rotate_session(session["token_hash"], user["id"]))

    def logout(self, environ: dict, session, user) -> Response:
        with transaction(self.config.database_path) as connection:
            if user:
                self.audit(connection, user["id"], "user.logout", "user", user["id"], environ)
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (session["token_hash"],))
        return self.redirect("/", self.cookie_value("deleted", delete=True))

    @staticmethod
    def validate_product(form: dict):
        values = {
            "title": form.get("title", "").strip(),
            "description": form.get("description", "").strip(),
            "category": form.get("category", ""),
            "item_condition": form.get("item_condition", ""),
            "price": form.get("price", ""),
        }
        issues = []
        if not 2 <= len(values["title"]) <= 80:
            issues.append("상품명은 2~80자로 입력해 주세요.")
        if not 10 <= len(values["description"]) <= 2000:
            issues.append("상품 설명은 10~2,000자로 입력해 주세요.")
        if values["category"] not in views.CATEGORIES:
            issues.append("카테고리를 선택해 주세요.")
        if values["item_condition"] not in views.CONDITIONS:
            issues.append("상품 상태를 선택해 주세요.")
        try:
            values["price"] = int(values["price"])
            if not 0 <= values["price"] <= 100_000_000:
                raise ValueError
        except (TypeError, ValueError):
            issues.append("가격은 0~100,000,000원 범위의 정수로 입력해 주세요.")
            values["price"] = form.get("price", "")
        return values, issues

    def store_image(self, upload) -> str:
        extension, data = validate_image(upload)
        filename = f"{secrets.token_hex(16)}.{extension}"
        with (self.upload_dir / filename).open("xb") as output:
            output.write(data)
        return filename

    def store_images(self, uploads) -> list[str]:
        if len(uploads) > 10:
            raise ValueError("사진은 한 번에 최대 10장까지 업로드할 수 있습니다.")
        filenames: list[str] = []
        try:
            for upload in uploads:
                filenames.append(self.store_image(upload))
        except Exception:
            for filename in filenames:
                self.remove_image(filename)
            raise
        return filenames

    def remove_image(self, filename: str | None) -> None:
        if filename and re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png|webp)", filename):
            try:
                (self.upload_dir / filename).unlink()
            except FileNotFoundError:
                pass

    def create_product(self, form, files, environ, user, csrf_token: str) -> Response:
        values, issues = self.validate_product(form)
        if issues:
            return self.page_response(views.product_form(product=values, error_items=issues, csrf_token=csrf_token, user=user), 400)
        image_filenames: list[str] = []
        uploads = files.get("images") or files.get("image") or []
        if not uploads:
            return self.page_response(views.product_form(product=values, error_items=["상품 사진을 선택해 주세요."], csrf_token=csrf_token, user=user), 400)
        try:
            image_filenames = self.store_images(uploads)
        except ValueError as error:
            return self.page_response(views.product_form(product=values, error_items=[str(error)], csrf_token=csrf_token, user=user), 400)
        try:
            with transaction(self.config.database_path) as connection:
                cursor = connection.execute(
                    "INSERT INTO products(seller_id,title,description,price,category,item_condition,image_filename) VALUES (?,?,?,?,?,?,?)",
                    (user["id"], values["title"], values["description"], values["price"], values["category"], values["item_condition"], image_filenames[0]),
                )
                connection.executemany(
                    "INSERT INTO product_images(product_id,filename,position) VALUES (?,?,?)",
                    [(cursor.lastrowid, filename, position) for position, filename in enumerate(image_filenames)],
                )
                self.audit(connection, user["id"], "product.create", "product", cursor.lastrowid, environ)
        except Exception:
            for filename in image_filenames:
                self.remove_image(filename)
            raise
        return self.redirect(f"/products/{cursor.lastrowid}")

    def product_route(self, product_id: int, action: str | None, method: str, form, files, environ, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            product = connection.execute("SELECT p.*,u.nickname seller_name,u.status seller_status,u.deleted_at seller_deleted_at FROM products p JOIN users u ON u.id=p.seller_id WHERE p.id=?", (product_id,)).fetchone()
            image_filenames = [row[0] for row in connection.execute("SELECT filename FROM product_images WHERE product_id=? ORDER BY position", (product_id,))] if product else []
            chat_image_filenames = [row[0] for row in connection.execute("SELECT mi.filename FROM message_images mi JOIN messages m ON m.id=mi.message_id WHERE m.product_id=?", (product_id,))] if product else []
            blocked = user and product and user["id"] != product["seller_id"] and connection.execute(
                "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?) LIMIT 1",
                (user["id"], product["seller_id"], product["seller_id"], user["id"]),
            ).fetchone()
        if not product:
            return self.error(404, "상품을 찾을 수 없습니다", "삭제되었거나 존재하지 않는 상품입니다.", user, csrf_token)
        if product["seller_deleted_at"] is not None:
            return self.error(404, "탈퇴한 회원입니다", "판매자가 탈퇴하여 이 상품 페이지를 이용할 수 없습니다.", user, csrf_token)
        product = dict(product)
        product["image_filenames"] = image_filenames or ([product["image_filename"]] if product["image_filename"] else [])
        if blocked and user.get("role") != "admin":
            return self.error(404, "상품을 찾을 수 없습니다", "차단 관계인 사용자의 상품은 이용할 수 없습니다.", user, csrf_token)
        if product["moderation_status"] == "hidden" and not (user and (user["id"] == product["seller_id"] or user.get("role") == "admin")):
            return self.error(404, "상품을 찾을 수 없습니다", "삭제되었거나 존재하지 않는 상품입니다.", user, csrf_token)
        if product["seller_status"] != "active" and not (user and (user["id"] == product["seller_id"] or user.get("role") == "admin")):
            return self.error(404, "상품을 찾을 수 없습니다", "활동이 제한된 판매자의 상품은 거래할 수 없습니다.", user, csrf_token)
        if action is None and method == "GET":
            return self.page_response(views.product_detail(product, user=user, csrf_token=csrf_token))
        if action == "edit" and method in {"GET", "POST"}:
            if not user or user["id"] != product["seller_id"]:
                return self.error(403, "권한이 없습니다", "판매자만 상품을 수정할 수 있습니다.", user, csrf_token)
            denied = self.require_active(user, csrf_token)
            if denied:
                return denied
            if product["status"] != "available":
                return self.error(409, "수정할 수 없습니다", "판매 완료된 상품은 수정할 수 없습니다.", user, csrf_token)
            if method == "GET":
                return self.page_response(views.product_form(product=product, csrf_token=csrf_token, user=user))
            return self.edit_product(product, form, files, environ, user, csrf_token)
        if action == "delete" and method == "POST":
            if not user or user["id"] != product["seller_id"]:
                return self.error(403, "권한이 없습니다", "판매자만 상품을 삭제할 수 있습니다.", user, csrf_token)
            with transaction(self.config.database_path) as connection:
                self.audit(connection, user["id"], "product.delete", "product", product_id, environ)
                connection.execute("UPDATE transfers SET product_id=NULL WHERE product_id=?", (product_id,))
                connection.execute("DELETE FROM products WHERE id=? AND seller_id=?", (product_id, user["id"]))
            for filename in product["image_filenames"] + chat_image_filenames:
                self.remove_image(filename)
            return self.redirect("/my")
        if action == "checkout" and method == "GET":
            denied = self.require_active(user, csrf_token)
            if denied:
                return denied
            if product["status"] != "available" or product["seller_id"] == user["id"]:
                return self.error(409, "송금할 수 없습니다", "판매 중인 다른 사용자의 상품만 구매할 수 있습니다.", user, csrf_token)
            return self.page_response(views.checkout_page(product, user=user, csrf_token=csrf_token))
        if action == "purchase" and method == "POST":
            return self.purchase(product_id, environ, user, csrf_token)
        return self.error(405, "허용되지 않는 요청", "이 주소에서는 해당 요청을 사용할 수 없습니다.", user, csrf_token)

    def edit_product(self, product, form, files, environ, user, csrf_token) -> Response:
        product_id = product["id"]
        values, issues = self.validate_product(form)
        values["id"] = product_id
        values["image_filename"] = product["image_filename"]
        if issues:
            return self.page_response(views.product_form(product=values, error_items=issues, csrf_token=csrf_token, user=user), 400)
        new_images: list[str] = []
        uploads = files.get("images") or files.get("image") or []
        if uploads:
            try:
                new_images = self.store_images(uploads)
                values["image_filename"] = new_images[0]
            except ValueError as error:
                return self.page_response(views.product_form(product=values, error_items=[str(error)], csrf_token=csrf_token, user=user), 400)
        try:
            with transaction(self.config.database_path) as connection:
                cursor = connection.execute(
                    "UPDATE products SET title=?,description=?,price=?,category=?,item_condition=?,image_filename=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND seller_id=? AND status='available'",
                    (values["title"], values["description"], values["price"], values["category"], values["item_condition"], values["image_filename"], product_id, user["id"]),
                )
                if cursor.rowcount != 1:
                    for filename in new_images:
                        self.remove_image(filename)
                    return self.error(409, "수정할 수 없습니다", "상품 상태가 변경되었습니다.", user, csrf_token)
                if new_images:
                    connection.execute("DELETE FROM product_images WHERE product_id=?", (product_id,))
                    connection.executemany(
                        "INSERT INTO product_images(product_id,filename,position) VALUES (?,?,?)",
                        [(product_id, filename, position) for position, filename in enumerate(new_images)],
                    )
                self.audit(connection, user["id"], "product.update", "product", product_id, environ)
        except Exception:
            for filename in new_images:
                self.remove_image(filename)
            raise
        if new_images:
            for filename in product.get("image_filenames", []):
                self.remove_image(filename)
        return self.redirect(f"/products/{product_id}")

    def purchase(self, product_id, environ, user, csrf_token) -> Response:
        denied = self.require_active(user, csrf_token)
        if denied:
            return denied
        with transaction(self.config.database_path, immediate=True) as connection:
            product = connection.execute(
                "SELECT p.seller_id,p.price FROM products p JOIN users s ON s.id=p.seller_id WHERE p.id=? AND p.status='available' AND p.moderation_status='visible' AND s.status='active'",
                (product_id,),
            ).fetchone()
            if not product or product["seller_id"] == user["id"]:
                return self.error(409, "구매할 수 없습니다", "이미 판매되었거나 본인의 상품입니다.", user, csrf_token)
            if connection.execute(
                "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?) LIMIT 1",
                (user["id"], product["seller_id"], product["seller_id"], user["id"]),
            ).fetchone():
                return self.error(403, "구매할 수 없습니다", "차단 관계인 사용자와는 거래할 수 없습니다.", user, csrf_token)
            debit = connection.execute(
                "UPDATE users SET balance=balance-? WHERE id=? AND status='active' AND balance>=?",
                (product["price"], user["id"], product["price"]),
            )
            if debit.rowcount != 1:
                return self.error(409, "잔액이 부족합니다", "보유 포인트를 확인해 주세요.", user, csrf_token)
            cursor = connection.execute(
                "UPDATE products SET status='sold',buyer_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='available' AND moderation_status='visible' AND seller_id<>?",
                (user["id"], product_id, user["id"]),
            )
            if cursor.rowcount != 1:
                return self.error(409, "구매할 수 없습니다", "이미 판매되었거나 본인의 상품입니다.", user, csrf_token)
            connection.execute("UPDATE users SET balance=balance+? WHERE id=?", (product["price"], product["seller_id"]))
            connection.execute(
                "INSERT INTO transfers(sender_id,recipient_id,product_id,amount) VALUES (?,?,?,?)",
                (user["id"], product["seller_id"], product_id, product["price"]),
            )
            self.audit(connection, user["id"], "product.purchase", "product", product_id, environ)
        return self.redirect(f"/products/{product_id}")

    def my_page(self, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            selling = connection.execute("SELECT * FROM products WHERE seller_id=? ORDER BY created_at DESC,id DESC", (user["id"],)).fetchall()
            bought = connection.execute("SELECT * FROM products WHERE buyer_id=? ORDER BY updated_at DESC,id DESC", (user["id"],)).fetchall()
            transfers = connection.execute(
                """SELECT t.*,p.title,s.nickname sender_name,r.nickname recipient_name
                   FROM transfers t LEFT JOIN products p ON p.id=t.product_id
                   JOIN users s ON s.id=t.sender_id JOIN users r ON r.id=t.recipient_id
                   WHERE t.sender_id=? OR t.recipient_id=? ORDER BY t.created_at DESC,t.id DESC LIMIT 50""",
                (user["id"], user["id"]),
            ).fetchall()
            blocked_users = connection.execute(
                "SELECT b.id,u.id user_id,u.nickname,b.created_at FROM user_blocks b JOIN users u ON u.id=b.blocked_id WHERE b.blocker_id=? ORDER BY b.created_at DESC",
                (user["id"],),
            ).fetchall()
        return self.page_response(views.my_page(selling, bought, transfers, blocked_users, user=user, csrf_token=csrf_token))

    def user_profile(self, user_id: int, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            profile = connection.execute("SELECT id,username,nickname,bio,status,deleted_at FROM users WHERE id=?", (user_id,)).fetchone()
            products = connection.execute("SELECT id,title,price FROM products WHERE seller_id=? AND status='available' AND moderation_status='visible' ORDER BY created_at DESC", (user_id,)).fetchall()
            is_blocked = bool(user and connection.execute("SELECT 1 FROM user_blocks WHERE blocker_id=? AND blocked_id=?", (user["id"], user_id)).fetchone())
        if profile and profile["deleted_at"] is not None:
            return self.error(404, "탈퇴한 회원입니다", "탈퇴한 회원의 프로필은 이용할 수 없습니다.", user, csrf_token)
        if not profile or profile["status"] != "active":
            return self.error(404, "사용자를 찾을 수 없습니다", "존재하지 않거나 이용이 제한된 사용자입니다.", user, csrf_token)
        return self.page_response(views.profile_page(profile, products, is_blocked=is_blocked, user=user, csrf_token=csrf_token))

    def withdraw_page(self, user, csrf_token: str, *, error_items=None, status: int = 200) -> Response:
        if user.get("role") == "admin":
            return self.error(403, "관리자는 탈퇴할 수 없습니다", "관리자 계정 보호를 위해 일반 회원만 직접 탈퇴할 수 있습니다.", user, csrf_token)
        return self.page_response(views.withdraw_page(user=user, csrf_token=csrf_token, error_items=error_items), status)

    def withdraw_account(self, form, environ, session, user, csrf_token: str) -> Response:
        if user.get("role") == "admin":
            return self.error(403, "관리자는 탈퇴할 수 없습니다", "관리자 계정 보호를 위해 일반 회원만 직접 탈퇴할 수 있습니다.", user, csrf_token)
        password = form.get("current_password", "")
        confirmed = form.get("confirmation", "").strip() == "회원탈퇴"
        with connect(self.config.database_path) as connection:
            account = connection.execute(
                "SELECT password_hash,deleted_at FROM users WHERE id=?",
                (user["id"],),
            ).fetchone()
        issues = []
        if not account or account["deleted_at"] is not None:
            return self.error(409, "이미 탈퇴한 회원입니다", "이 계정은 더 이상 이용할 수 없습니다.", None, csrf_token)
        if not verify_password(password, account["password_hash"]):
            issues.append("현재 비밀번호가 올바르지 않습니다.")
        if not confirmed:
            issues.append("확인란에 ‘회원탈퇴’를 정확히 입력해 주세요.")
        if issues:
            return self.withdraw_page(user, csrf_token, error_items=issues, status=400)

        # '-'는 일반 가입 아이디·닉네임의 허용 문자에 없으므로 기존 회원값과
        # 충돌하지 않으면서 레코드 참조 무결성을 유지하는 익명 식별자가 된다.
        anonymous_username = f"withdrawn-{user['id']}"[:24]
        anonymous_nickname = f"탈퇴한회원-{user['id']}"[:20]
        disabled_password = hash_password(new_session_token())
        with transaction(self.config.database_path, immediate=True) as connection:
            current = connection.execute(
                "SELECT password_hash,deleted_at,role FROM users WHERE id=?",
                (user["id"],),
            ).fetchone()
            if not current or current["deleted_at"] is not None or current["role"] == "admin" or current["password_hash"] != account["password_hash"]:
                return self.error(409, "탈퇴할 수 없습니다", "계정 상태가 변경되었습니다.", user, csrf_token)
            connection.execute(
                """UPDATE users
                   SET username=?,nickname=?,password_hash=?,bio='',status='suspended',
                       deleted_at=CURRENT_TIMESTAMP
                   WHERE id=? AND deleted_at IS NULL AND role<>'admin'""",
                (anonymous_username, anonymous_nickname, disabled_password, user["id"]),
            )
            connection.execute(
                "UPDATE products SET moderation_status='hidden',updated_at=CURRENT_TIMESTAMP WHERE seller_id=?",
                (user["id"],),
            )
            connection.execute(
                "DELETE FROM user_blocks WHERE blocker_id=? OR blocked_id=?",
                (user["id"], user["id"]),
            )
            self.audit(connection, user["id"], "user.withdraw", "user", user["id"], environ)
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        return self.redirect("/", self.cookie_value("", delete=True))

    def update_profile(self, form, environ, user, csrf_token: str) -> Response:
        bio = form.get("bio", "").strip()
        nickname = form.get("nickname", "").strip()
        current_password = form.get("current_password", "")
        new_password = form.get("new_password", "")
        issues = []
        if len(bio) > 300:
            issues.append("소개글은 300자 이하여야 합니다.")
        if not NICKNAME_RE.fullmatch(nickname):
            issues.append("닉네임은 2~20자의 한글, 영문, 숫자, 밑줄만 사용할 수 있습니다.")
        with connect(self.config.database_path) as connection:
            encoded = connection.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()[0]
        if not verify_password(current_password, encoded):
            issues.append("현재 비밀번호가 올바르지 않습니다.")
        if new_password:
            issues.extend(validate_password(new_password))
        if issues:
            preview = dict(user)
            preview["bio"] = bio
            preview["nickname"] = nickname
            return self.page_response(views.profile_edit(user=preview, csrf_token=csrf_token, error_items=issues), 400)
        try:
            with transaction(self.config.database_path) as connection:
                if new_password:
                    connection.execute("UPDATE users SET nickname=?,bio=?,password_hash=? WHERE id=?", (nickname, bio, hash_password(new_password), user["id"]))
                    connection.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user["id"], token_hash(parse_cookies(environ)["tiny_session"].value)))
                else:
                    connection.execute("UPDATE users SET nickname=?,bio=? WHERE id=?", (nickname, bio, user["id"]))
                self.audit(connection, user["id"], "user.profile_update", "user", user["id"], environ)
        except sqlite3.IntegrityError:
            preview = dict(user)
            preview["bio"] = bio
            preview["nickname"] = nickname
            return self.page_response(views.profile_edit(user=preview, csrf_token=csrf_token, error_items=["이미 사용 중인 닉네임입니다."]), 409)
        return self.redirect("/my")

    def chat_inbox(self, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            conversations = connection.execute(
                """SELECT m.product_id,p.title,p.status product_status,
                   CASE WHEN m.sender_id=? THEN m.recipient_id ELSE m.sender_id END counterpart_id,
                   u.nickname counterpart_name,MAX(m.created_at) last_at,
                   SUM(CASE WHEN m.recipient_id=? AND m.read_at IS NULL THEN 1 ELSE 0 END) unread_count
                   FROM messages m JOIN products p ON p.id=m.product_id
                   JOIN users u ON u.id=CASE WHEN m.sender_id=? THEN m.recipient_id ELSE m.sender_id END
                   WHERE m.product_id IS NOT NULL AND (m.sender_id=? OR m.recipient_id=?)
                   GROUP BY m.product_id,counterpart_id,u.nickname,p.title ORDER BY last_at DESC""",
                (user["id"], user["id"], user["id"], user["id"], user["id"]),
            ).fetchall()
        return self.page_response(views.chat_inbox(conversations, user=user, csrf_token=csrf_token))

    def chat_page(self, user, csrf_token: str, product_id: int, counterpart_id: int | None = None) -> Response:
        with connect(self.config.database_path) as connection:
            product = connection.execute(
                "SELECT p.id,p.title,p.seller_id,p.buyer_id,u.nickname seller_name FROM products p JOIN users u ON u.id=p.seller_id WHERE p.id=?",
                (product_id,),
            ).fetchone()
            if not product:
                return self.error(404, "상품을 찾을 수 없습니다", "삭제되었거나 존재하지 않는 상품입니다.", user, csrf_token)
            if user["id"] == product["seller_id"]:
                if not counterpart_id:
                    return self.redirect("/chat")
                allowed = connection.execute(
                    "SELECT 1 FROM messages WHERE product_id=? AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)) LIMIT 1",
                    (product_id, user["id"], counterpart_id, counterpart_id, user["id"]),
                ).fetchone()
                if not allowed and product["buyer_id"] != counterpart_id:
                    return self.error(403, "대화 권한이 없습니다", "해당 상품의 대화 상대가 아닙니다.", user, csrf_token)
            else:
                if counterpart_id and counterpart_id != product["seller_id"]:
                    return self.error(403, "대화 권한이 없습니다", "상품 판매자와만 대화할 수 있습니다.", user, csrf_token)
                counterpart_id = product["seller_id"]
            counterpart = connection.execute("SELECT id,nickname FROM users WHERE id=? AND status='active'", (counterpart_id,)).fetchone()
            if not counterpart:
                return self.error(404, "대화 상대를 찾을 수 없습니다", "이용 가능한 판매자가 아닙니다.", user, csrf_token)
            if connection.execute(
                "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?) LIMIT 1",
                (user["id"], counterpart_id, counterpart_id, user["id"]),
            ).fetchone():
                return self.error(403, "대화할 수 없습니다", "차단 관계인 사용자와는 메시지를 주고받을 수 없습니다.", user, csrf_token)
            messages = connection.execute(
                """SELECT m.*,u.nickname sender_name,
                   (SELECT GROUP_CONCAT(filename,'|') FROM (SELECT filename FROM message_images WHERE message_id=m.id ORDER BY position)) image_filenames
                   FROM messages m JOIN users u ON u.id=m.sender_id
                   WHERE m.product_id=? AND ((m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?))
                   ORDER BY m.created_at DESC,m.id DESC LIMIT 100""",
                (product_id, user["id"], counterpart_id, counterpart_id, user["id"]),
            ).fetchall()
            connection.execute(
                "UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE product_id=? AND sender_id=? AND recipient_id=? AND read_at IS NULL",
                (product_id, counterpart_id, user["id"]),
            )
        user = dict(user)
        user["unread_count"] = max(0, user.get("unread_count", 0) - sum(1 for message in messages if message["recipient_id"] == user["id"] and message["read_at"] is None))
        return self.page_response(views.chat_page(messages, product, counterpart, user=user, csrf_token=csrf_token))

    def send_message(self, form, files, environ, user, csrf_token: str) -> Response:
        body = form.get("body", "").strip()
        uploads = files.get("images") or files.get("image") or []
        product_text = form.get("product_id", "").strip()
        counterpart_text = form.get("counterpart_id", "").strip()
        if len(body) > 500 or (not body and not uploads):
            return self.error(400, "메시지를 보낼 수 없습니다", "메시지 또는 사진을 입력해 주세요. 글은 최대 500자입니다.", user, csrf_token)
        if not product_text.isdigit():
            return self.error(400, "메시지를 보낼 수 없습니다", "올바른 상품에서 대화를 시작해 주세요.", user, csrf_token)
        product_id = int(product_text)
        image_filenames: list[str] = []
        try:
            with transaction(self.config.database_path) as connection:
                product = connection.execute("SELECT id,seller_id,buyer_id FROM products WHERE id=?", (product_id,)).fetchone()
                if not product:
                    return self.error(404, "상품을 찾을 수 없습니다", "삭제되었거나 존재하지 않는 상품입니다.", user, csrf_token)
                if user["id"] == product["seller_id"]:
                    if not counterpart_text.isdigit():
                        return self.error(400, "메시지를 보낼 수 없습니다", "대화 상대를 다시 선택해 주세요.", user, csrf_token)
                    recipient_id = int(counterpart_text)
                    allowed = connection.execute(
                        "SELECT 1 FROM messages WHERE product_id=? AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)) LIMIT 1",
                        (product_id, user["id"], recipient_id, recipient_id, user["id"]),
                    ).fetchone()
                    if not allowed and product["buyer_id"] != recipient_id:
                        return self.error(403, "메시지를 보낼 수 없습니다", "해당 상품의 대화 상대가 아닙니다.", user, csrf_token)
                else:
                    recipient_id = product["seller_id"]
                if recipient_id == user["id"] or not connection.execute("SELECT 1 FROM users WHERE id=? AND status='active'", (recipient_id,)).fetchone():
                    return self.error(404, "대화 상대를 찾을 수 없습니다", "이용 가능한 판매자가 아닙니다.", user, csrf_token)
                if connection.execute(
                    "SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?) LIMIT 1",
                    (user["id"], recipient_id, recipient_id, user["id"]),
                ).fetchone():
                    return self.error(403, "메시지를 보낼 수 없습니다", "차단 관계인 사용자와는 메시지를 주고받을 수 없습니다.", user, csrf_token)
                if uploads:
                    try:
                        image_filenames = self.store_images(uploads)
                    except ValueError as error:
                        return self.error(400, "사진을 보낼 수 없습니다", str(error), user, csrf_token)
                cursor = connection.execute(
                    "INSERT INTO messages(sender_id,recipient_id,product_id,body,image_filename) VALUES (?,?,?,?,?)",
                    (user["id"], recipient_id, product_id, body or "사진을 보냈습니다.", image_filenames[0] if image_filenames else None),
                )
                connection.executemany(
                    "INSERT INTO message_images(message_id,filename,position) VALUES (?,?,?)",
                    [(cursor.lastrowid, filename, position) for position, filename in enumerate(image_filenames)],
                )
                self.audit(connection, user["id"], "message.send", "product", product_id, environ)
        except Exception:
            for filename in image_filenames:
                self.remove_image(filename)
            raise
        suffix = f"/{recipient_id}" if user["id"] == product["seller_id"] else ""
        return self.redirect(f"/chat/{product_id}{suffix}")

    def toggle_block(self, target_id: int, environ, user, csrf_token: str) -> Response:
        if target_id == user["id"]:
            return self.error(400, "차단할 수 없습니다", "본인 계정은 차단할 수 없습니다.", user, csrf_token)
        with transaction(self.config.database_path, immediate=True) as connection:
            target = connection.execute("SELECT role,deleted_at FROM users WHERE id=?", (target_id,)).fetchone()
            if not target or target["role"] == "admin" or target["deleted_at"] is not None:
                return self.error(404, "사용자를 찾을 수 없습니다", "일반 사용자만 차단할 수 있습니다.", user, csrf_token)
            existing = connection.execute("SELECT id FROM user_blocks WHERE blocker_id=? AND blocked_id=?", (user["id"], target_id)).fetchone()
            if existing:
                connection.execute("DELETE FROM user_blocks WHERE id=?", (existing["id"],))
                event = "user.unblock"
            else:
                connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id) VALUES (?,?)", (user["id"], target_id))
                event = "user.block"
            self.audit(connection, user["id"], event, "user", target_id, environ)
        return self.redirect(f"/users/{target_id}")

    def report(self, target_type: str, target_id: int, method: str, form, environ, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            if target_type == "product":
                target = connection.execute("SELECT * FROM products WHERE id=?", (target_id,)).fetchone()
            else:
                target = connection.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
        if not target or (target_type == "product" and target["seller_id"] == user["id"]) or (target_type == "user" and (target_id == user["id"] or target["deleted_at"] is not None)):
            return self.error(404, "신고 대상을 찾을 수 없습니다", "본인 또는 존재하지 않는 대상은 신고할 수 없습니다.", user, csrf_token)
        if method == "GET":
            return self.page_response(views.report_page(target_type, target, user=user, csrf_token=csrf_token))
        reason = form.get("reason", "").strip()
        if not 10 <= len(reason) <= 500:
            return self.page_response(views.report_page(target_type, target, user=user, csrf_token=csrf_token, error_items=["신고 사유는 10~500자로 작성해 주세요."]), 400)
        try:
            with transaction(self.config.database_path, immediate=True) as connection:
                connection.execute("INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES (?,?,?,?)", (user["id"], target_type, target_id, reason))
                count = connection.execute("SELECT COUNT(*) FROM reports WHERE target_type=? AND target_id=? AND status='open'", (target_type, target_id)).fetchone()[0]
                if target_type == "product" and count >= 3:
                    connection.execute("UPDATE products SET moderation_status='hidden' WHERE id=?", (target_id,))
                elif target_type == "user" and count >= 5:
                    connection.execute("UPDATE users SET status='suspended' WHERE id=? AND role<>'admin'", (target_id,))
                self.audit(connection, user["id"], "report.create", target_type, target_id, environ)
        except sqlite3.IntegrityError:
            return self.error(409, "이미 신고했습니다", "같은 대상은 한 번만 신고할 수 있습니다.", user, csrf_token)
        return self.redirect("/")

    def chat_report(self, product_id: int, counterpart_id: int, method: str, form, environ, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            product = connection.execute("SELECT id,title FROM products WHERE id=?", (product_id,)).fetchone()
            counterpart = connection.execute("SELECT id,nickname FROM users WHERE id=?", (counterpart_id,)).fetchone()
            participated = connection.execute(
                """SELECT 1 FROM messages
                   WHERE product_id=? AND
                   ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?))
                   LIMIT 1""",
                (product_id, user["id"], counterpart_id, counterpart_id, user["id"]),
            ).fetchone()
        if not product or not counterpart or counterpart_id == user["id"] or not participated:
            return self.error(
                404, "신고할 채팅을 찾을 수 없습니다",
                "실제로 참여한 1:1 상품 채팅만 신고할 수 있습니다.", user, csrf_token,
            )

        context = f'[채팅 신고 · 상품 #{product_id}: {product["title"][:80]}] '
        max_reason_length = 500 - len(context)
        if method == "GET":
            return self.page_response(views.chat_report_page(
                counterpart, product, user=user, csrf_token=csrf_token,
                max_reason_length=max_reason_length,
            ))

        reason = form.get("reason", "").strip()
        if not 10 <= len(reason) <= max_reason_length:
            return self.page_response(views.chat_report_page(
                counterpart, product, user=user, csrf_token=csrf_token,
                max_reason_length=max_reason_length, reason=reason,
                error_items=[f"신고 사유는 10~{max_reason_length}자로 작성해 주세요."],
            ), 400)
        try:
            with transaction(self.config.database_path, immediate=True) as connection:
                connection.execute(
                    "INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES (?,?,?,?)",
                    (user["id"], "user", counterpart_id, context + reason),
                )
                count = connection.execute(
                    "SELECT COUNT(*) FROM reports WHERE target_type='user' AND target_id=? AND status='open'",
                    (counterpart_id,),
                ).fetchone()[0]
                if count >= 5:
                    connection.execute(
                        "UPDATE users SET status='suspended' WHERE id=? AND role<>'admin'",
                        (counterpart_id,),
                    )
                self.audit(connection, user["id"], "report.chat_create", "user", counterpart_id, environ)
        except sqlite3.IntegrityError:
            return self.error(
                409, "이미 신고했습니다",
                "같은 사용자는 일반 신고와 채팅 신고를 합쳐 한 번만 신고할 수 있습니다.",
                user, csrf_token,
            )
        return self.redirect(f"/chat/{product_id}/{counterpart_id}")

    def admin_page(self, user, csrf_token: str) -> Response:
        with connect(self.config.database_path) as connection:
            users = connection.execute(
                """SELECT u.id,u.username,u.nickname,u.role,u.status,u.deleted_at,u.balance,
                   COUNT(r.id) user_report_count,GROUP_CONCAT(CASE WHEN r.status='open' THEN r.reason END,' / ') open_report_reasons
                   FROM users u LEFT JOIN reports r ON r.target_type='user' AND r.target_id=u.id
                   GROUP BY u.id,u.username,u.nickname,u.role,u.status,u.deleted_at,u.balance ORDER BY u.created_at DESC"""
            ).fetchall()
            products = connection.execute(
                """SELECT p.id,p.title,p.moderation_status,p.image_filename,u.nickname seller_name,
                   COUNT(r.id) report_count,GROUP_CONCAT(r.reason,' / ') report_reasons
                   FROM products p JOIN users u ON u.id=p.seller_id
                   JOIN reports r ON r.target_type='product' AND r.target_id=p.id
                   GROUP BY p.id,p.title,p.moderation_status,p.image_filename,u.nickname ORDER BY MAX(r.created_at) DESC"""
            ).fetchall()
            reports = connection.execute(
                """SELECT r.*,u.nickname reporter_name,
                   CASE WHEN r.target_type='user' THEN tu.nickname ELSE p.title END target_name
                   FROM reports r JOIN users u ON u.id=r.reporter_id
                   LEFT JOIN users tu ON r.target_type='user' AND tu.id=r.target_id
                   LEFT JOIN products p ON r.target_type='product' AND p.id=r.target_id
                   ORDER BY CASE r.status WHEN 'open' THEN 0 ELSE 1 END,r.created_at DESC LIMIT 200"""
            ).fetchall()
            blocks = connection.execute(
                """SELECT b.id,b.created_at,a.nickname blocker_name,z.nickname blocked_name
                   FROM user_blocks b JOIN users a ON a.id=b.blocker_id JOIN users z ON z.id=b.blocked_id
                   ORDER BY b.created_at DESC LIMIT 200"""
            ).fetchall()
        return self.page_response(views.admin_page(users, products, reports, blocks, user=user, csrf_token=csrf_token))

    def admin_report_action(self, report_id: int, action: str, environ, user, csrf_token: str) -> Response:
        status = {"resolve": "resolved", "dismiss": "dismissed", "reopen": "open"}[action]
        with transaction(self.config.database_path) as connection:
            cursor = connection.execute("UPDATE reports SET status=? WHERE id=?", (status, report_id))
            if cursor.rowcount != 1:
                return self.error(404, "신고를 찾을 수 없습니다", "신고 기록을 다시 확인해 주세요.", user, csrf_token)
            self.audit(connection, user["id"], f"admin.report_{action}", "report", report_id, environ)
        return self.redirect("/admin")

    def admin_remove_block(self, block_id: int, environ, user, csrf_token: str) -> Response:
        with transaction(self.config.database_path) as connection:
            cursor = connection.execute("DELETE FROM user_blocks WHERE id=?", (block_id,))
            if cursor.rowcount != 1:
                return self.error(404, "차단 기록을 찾을 수 없습니다", "차단 기록을 다시 확인해 주세요.", user, csrf_token)
            self.audit(connection, user["id"], "admin.block_remove", "block", block_id, environ)
        return self.redirect("/admin")

    def admin_action(self, target_type: str, target_id: int, action: str, environ, user, csrf_token: str) -> Response:
        images_to_remove: list[str] = []
        with transaction(self.config.database_path, immediate=True) as connection:
            if target_type == "user":
                if action != "toggle":
                    return self.error(405, "허용되지 않는 요청", "사용자에게는 상태 전환만 사용할 수 있습니다.", user, csrf_token)
                if target_id == user["id"]:
                    return self.error(409, "상태를 바꿀 수 없습니다", "현재 로그인한 관리자 계정은 정지할 수 없습니다.", user, csrf_token)
                target = connection.execute("SELECT status,role,deleted_at FROM users WHERE id=?", (target_id,)).fetchone()
                if not target or target["role"] == "admin":
                    return self.error(404, "사용자를 찾을 수 없습니다", "일반 사용자만 관리할 수 있습니다.", user, csrf_token)
                if target["deleted_at"] is not None:
                    return self.error(409, "탈퇴한 회원입니다", "탈퇴한 계정은 다시 활성화할 수 없습니다.", user, csrf_token)
                new_status = "active" if target["status"] == "suspended" else "suspended"
                connection.execute("UPDATE users SET status=? WHERE id=?", (new_status, target_id))
            else:
                target = connection.execute("SELECT moderation_status,image_filename FROM products WHERE id=?", (target_id,)).fetchone()
                if not target:
                    return self.error(404, "상품을 찾을 수 없습니다", "상품을 다시 확인해 주세요.", user, csrf_token)
                if action == "delete":
                    reported = connection.execute("SELECT 1 FROM reports WHERE target_type='product' AND target_id=? LIMIT 1", (target_id,)).fetchone()
                    if not reported:
                        return self.error(403, "삭제할 수 없습니다", "신고가 접수된 상품만 관리자 삭제가 가능합니다.", user, csrf_token)
                    images_to_remove = [row[0] for row in connection.execute("SELECT filename FROM product_images WHERE product_id=?", (target_id,))]
                    images_to_remove += [row[0] for row in connection.execute("SELECT mi.filename FROM message_images mi JOIN messages m ON m.id=mi.message_id WHERE m.product_id=?", (target_id,))]
                    if not images_to_remove and target["image_filename"]:
                        images_to_remove = [target["image_filename"]]
                    connection.execute("UPDATE transfers SET product_id=NULL WHERE product_id=?", (target_id,))
                    connection.execute("DELETE FROM products WHERE id=?", (target_id,))
                    connection.execute("UPDATE reports SET status='resolved' WHERE target_type='product' AND target_id=?", (target_id,))
                elif action == "toggle":
                    new_status = "visible" if target["moderation_status"] == "hidden" else "hidden"
                    connection.execute("UPDATE products SET moderation_status=? WHERE id=?", (new_status, target_id))
                else:
                    return self.error(405, "허용되지 않는 요청", "지원하지 않는 관리자 작업입니다.", user, csrf_token)
            self.audit(connection, user["id"], f"admin.{target_type}_{action}", target_type, target_id, environ)
        for filename in images_to_remove:
            self.remove_image(filename)
        return self.redirect("/admin")

    @staticmethod
    def audit(connection, user_id, event, target_type, target_id, environ) -> None:
        connection.execute(
            "INSERT INTO audit_log(user_id,event,target_type,target_id,ip_hash) VALUES (?,?,?,?,?)",
            (user_id, event, target_type, target_id, privacy_hash(environ.get("REMOTE_ADDR", "unknown"))),
        )


def create_app(config: Config | None = None) -> TinyMarketApp:
    return TinyMarketApp(config or Config.from_env())
