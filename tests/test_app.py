from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
import base64
import sqlite3
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from PIL import Image, PngImagePlugin

from tiny_market.app import FAKE_PASSWORD_HASH, create_app
from tiny_market.config import Config
from tiny_market.db import connect
from tiny_market.security import UploadedFile, hash_password, token_hash, validate_image, verify_password
from scripts.bootstrap_admin import main as bootstrap_admin


def png_bytes(*, metadata: bool = False) -> bytes:
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("Comment", "PRIVATE-METADATA-MARKER")
    Image.new("RGB", (4, 4), (255, 216, 77)).save(output, format="PNG", pnginfo=info)
    return output.getvalue()


class Client:
    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def request(self, method="GET", path="/", data=None, files=None):
        content_type = "application/x-www-form-urlencoded"
        if files:
            boundary = "----TinyMarketTestBoundary"
            chunks = []
            for key, value in (data or {}).items():
                chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
            for key, file_values in files.items():
                if isinstance(file_values, tuple):
                    file_values = [file_values]
                for filename, file_type, file_data in file_values:
                    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\nContent-Type: {file_type}\r\n\r\n".encode() + file_data + b"\r\n")
            chunks.append(f"--{boundary}--\r\n".encode())
            encoded = b"".join(chunks)
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            encoded = urlencode(data or {}).encode()
        path_info, _, query = path.partition("?")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(encoded)),
            "CONTENT_TYPE": content_type,
            "wsgi.input": io.BytesIO(encoded),
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_COOKIE": self.cookie,
        }
        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = headers

        body = b"".join(self.app(environ, start_response)).decode("utf-8", "replace")
        for key, value in captured["headers"]:
            if key.lower() == "set-cookie":
                self.cookie = value.split(";", 1)[0]
        return captured["status"], dict(captured["headers"]), body

    def csrf(self):
        _, _, body = self.request("GET", "/")
        return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1) if "csrf_token" in body else self.db_csrf()

    def db_csrf(self):
        raw = self.cookie.split("=", 1)[1]
        with connect(self.app.config.database_path) as connection:
            return connection.execute("SELECT csrf_token FROM sessions WHERE token_hash=?", (token_hash(raw),)).fetchone()[0]

    def login_as(self, user_id):
        from tiny_market.security import new_csrf_token, new_session_token, session_expiry
        raw = new_session_token()
        with connect(self.app.config.database_path) as connection:
            connection.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES (?,?,?,?)", (token_hash(raw), user_id, new_csrf_token(), session_expiry()))
        self.cookie = f"tiny_session={raw}"


class TinyMarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        config = Config(Path(self.temp.name) / "test.db", "127.0.0.1", 0, False, False)
        self.app = create_app(config)
        self.client = Client(self.app)

    def tearDown(self):
        self.temp.cleanup()

    def seed_user(self, name, *, role="user", balance=100_000):
        with connect(self.app.config.database_path) as connection:
            cursor = connection.execute("INSERT INTO users(username,nickname,password_hash,role,balance) VALUES (?,?,?,?,?)", (name, f"nick_{name}", "test-only", role, balance))
            return cursor.lastrowid

    def seed_product(self, seller_id, *, title="안전한 자전거", price=25_000):
        with connect(self.app.config.database_path) as connection:
            cursor = connection.execute("INSERT INTO products(seller_id,title,description,price,category,item_condition) VALUES (?,?,?,?,?,?)", (seller_id, title, "상태가 좋은 중고 상품입니다.", price, "sports", "good"))
            return cursor.lastrowid

    def test_password_hash_is_salted_and_verifiable(self):
        first = hash_password("StrongPass123", iterations=100_000)
        second = hash_password("StrongPass123", iterations=100_000)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("StrongPass123", first))
        self.assertFalse(verify_password("wrong", first))
        self.assertEqual(int(FAKE_PASSWORD_HASH.split("$")[1]), 600_000)

    def test_login_rate_limit_applies_across_different_usernames(self):
        self.client.request("GET", "/login")
        csrf = self.client.db_csrf()
        for index in range(20):
            status, _, _ = self.client.request(
                "POST", "/login",
                {"csrf_token": csrf, "username": f"unknown_{index}", "password": "WrongPassword123"},
            )
            self.assertEqual(status, 401)
        status, _, _ = self.client.request(
            "POST", "/login",
            {"csrf_token": csrf, "username": "another_unknown", "password": "WrongPassword123"},
        )
        self.assertEqual(status, 429)

    def test_security_headers_and_cookie_flags(self):
        status, headers, _ = self.client.request()
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        cookie = next(value for key, value in headers.items() if key == "Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_health_check_does_not_create_a_session(self):
        status, headers, body = self.client.request("GET", "/health")
        self.assertEqual((status, body), (200, "ok"))
        self.assertNotIn("Set-Cookie", headers)

    def test_deployment_admin_bootstrap_is_idempotent(self):
        deployment_db = Path(self.temp.name) / "deployment.db"
        environment = {
            "TINY_MARKET_DB": str(deployment_db),
            "TINY_MARKET_ADMIN_USERNAME": "deployment_admin",
            "TINY_MARKET_ADMIN_PASSWORD": "StrongDeploy123!",
        }
        with patch.dict(os.environ, environment, clear=True):
            bootstrap_admin()
            bootstrap_admin()
        with connect(deployment_db) as connection:
            admins = connection.execute(
                "SELECT username, role FROM users WHERE username='deployment_admin'"
            ).fetchall()
        self.assertEqual([(row["username"], row["role"]) for row in admins], [("deployment_admin", "admin")])

    def test_csrf_blocks_state_change(self):
        user_id = self.seed_user("seller")
        self.client.login_as(user_id)
        status, _, _ = self.client.request("POST", "/products/new", {"title": "공격"})
        self.assertEqual(status, 403)

    def test_xss_is_escaped_and_search_is_parameterized(self):
        seller = self.seed_user("seller")
        self.seed_product(seller, title='<script>alert("x")</script>')
        safe_product = self.seed_product(seller, title="가방")
        payloads = [
            "' OR 1=1--",
            "' UNION SELECT password_hash FROM users--",
            "'; DROP TABLE products;--",
            "%' OR 'x'='x",
        ]
        for payload in payloads:
            status, _, body = self.client.request("GET", "/?" + urlencode({"q": payload}))
            self.assertEqual(status, 200)
            self.assertNotIn("가방", body)
            self.assertNotIn("test-only", body)
        with connect(self.app.config.database_path) as connection:
            self.assertIsNotNone(connection.execute("SELECT id FROM products WHERE id=?", (safe_product,)).fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        _, _, body = self.client.request("GET", "/")
        self.assertNotIn('<script>alert("x")</script>', body)
        self.assertIn("&lt;script&gt;", body)

    def test_search_matches_any_character_in_product_title(self):
        seller = self.seed_user("searchseller")
        self.seed_product(seller, title="빨간 가방")
        self.seed_product(seller, title="파란 모자")
        self.seed_product(seller, title="노트북")
        _, _, body = self.client.request("GET", "/?q=%EA%B0%80%EB%AA%A8")
        self.assertIn("빨간 가방", body)
        self.assertIn("파란 모자", body)
        self.assertNotIn("노트북", body)

    def test_category_filter_combines_with_search_and_access_rules(self):
        seller = self.seed_user("categoryseller")
        blocked_seller = self.seed_user("blockedseller")
        suspended_seller = self.seed_user("suspendedseller")
        viewer = self.seed_user("viewer")
        laptop = self.seed_product(seller, title="노란 노트북")
        bag = self.seed_product(seller, title="파란 가방")
        blocked_product = self.seed_product(blocked_seller, title="차단 판매자 노트북")
        suspended_product = self.seed_product(suspended_seller, title="정지 판매자 노트북")
        with connect(self.app.config.database_path) as connection:
            connection.execute("UPDATE products SET category='digital' WHERE id IN (?,?,?)", (laptop, blocked_product, suspended_product))
            connection.execute("UPDATE products SET category='fashion' WHERE id=?", (bag,))
            connection.execute("UPDATE users SET status='suspended' WHERE id=?", (suspended_seller,))
            connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id) VALUES (?,?)", (viewer, blocked_seller))

        self.client.login_as(viewer)
        status, _, body = self.client.request("GET", "/?category=digital")
        self.assertEqual(status, 200)
        self.assertIn("노란 노트북", body)
        self.assertNotIn("파란 가방", body)
        self.assertNotIn("차단 판매자 노트북", body)
        self.assertNotIn("정지 판매자 노트북", body)
        self.assertIn('name="category" value="digital"', body)

        status, _, body = self.client.request("GET", "/?category=digital&q=%EB%85%B8")
        self.assertEqual(status, 200)
        self.assertIn("노란 노트북", body)
        status, _, body = self.client.request("GET", "/?category=fashion&q=%EB%85%B8")
        self.assertEqual(status, 200)
        self.assertNotIn("노란 노트북", body)

        status, _, _ = self.client.request("GET", "/?category=digital%27%20OR%201%3D1--")
        self.assertEqual(status, 400)
        with connect(self.app.config.database_path) as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM products WHERE id=?", (laptop,)).fetchone())

    def test_only_owner_can_edit_or_delete(self):
        seller = self.seed_user("seller")
        attacker = self.seed_user("attacker")
        product = self.seed_product(seller)
        self.client.login_as(attacker)
        csrf = self.client.db_csrf()
        status, _, _ = self.client.request("POST", f"/products/{product}/edit", {"csrf_token": csrf})
        self.assertEqual(status, 403)
        status, _, _ = self.client.request("POST", f"/products/{product}/delete", {"csrf_token": csrf})
        self.assertEqual(status, 403)

    def test_purchase_moves_balance_once(self):
        seller = self.seed_user("seller", balance=100)
        buyer = self.seed_user("buyer", balance=30_000)
        product = self.seed_product(seller, price=25_000)
        self.client.login_as(buyer)
        csrf = self.client.db_csrf()
        status, headers, _ = self.client.request("POST", f"/products/{product}/purchase", {"csrf_token": csrf})
        self.assertEqual(status, 302)
        status, _, _ = self.client.request("POST", f"/products/{product}/purchase", {"csrf_token": csrf})
        self.assertEqual(status, 409)
        with connect(self.app.config.database_path) as connection:
            balances = {row["username"]: row["balance"] for row in connection.execute("SELECT username,balance FROM users")}
            transfer_count = connection.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
        self.assertEqual(balances, {"seller": 25_100, "buyer": 5_000})
        self.assertEqual(transfer_count, 1)

    def test_checkout_explicitly_describes_point_transfer(self):
        seller = self.seed_user("seller", balance=100)
        buyer = self.seed_user("buyer", balance=30_000)
        product = self.seed_product(seller, price=25_000)
        self.client.login_as(buyer)
        status, _, body = self.client.request("GET", f"/products/{product}/checkout")
        self.assertEqual(status, 200)
        self.assertIn("교육용 포인트 송금", body)
        self.assertIn("25,000원", body)
        self.assertIn("5,000원", body)
        self.assertIn("받는 사람", body)
        self.assertIn(f'action="/products/{product}/purchase"', body)

    def test_insufficient_balance_keeps_item_available(self):
        seller = self.seed_user("seller")
        buyer = self.seed_user("buyer", balance=10)
        product = self.seed_product(seller, price=25_000)
        self.client.login_as(buyer)
        status, _, _ = self.client.request("POST", f"/products/{product}/purchase", {"csrf_token": self.client.db_csrf()})
        self.assertEqual(status, 409)
        with connect(self.app.config.database_path) as connection:
            item = connection.execute("SELECT status,buyer_id FROM products WHERE id=?", (product,)).fetchone()
        self.assertEqual(item["status"], "available")
        self.assertIsNone(item["buyer_id"])

    def test_three_unique_reports_hide_product(self):
        seller = self.seed_user("seller")
        product = self.seed_product(seller)
        for number in range(3):
            reporter = self.seed_user(f"reporter{number}")
            client = Client(self.app)
            client.login_as(reporter)
            status, _, _ = client.request("POST", f"/report/product/{product}", {"csrf_token": client.db_csrf(), "reason": "허위 상품으로 의심되는 충분한 사유"})
            self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            state = connection.execute("SELECT moderation_status FROM products WHERE id=?", (product,)).fetchone()[0]
        self.assertEqual(state, "hidden")
        status, _, _ = self.client.request("GET", f"/products/{product}")
        self.assertEqual(status, 404)

    def test_admin_endpoints_require_live_server_side_admin_session(self):
        normal = self.seed_user("normal")
        target = self.seed_user("target")
        admin = self.seed_user("adminuser", role="admin")
        product = self.seed_product(normal)
        with connect(self.app.config.database_path) as connection:
            report_id = connection.execute(
                "INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES (?,?,?,?)",
                (normal, "user", target, "관리자 권한 검사를 위한 충분한 신고 사유"),
            ).lastrowid
            block_id = connection.execute(
                "INSERT INTO user_blocks(blocker_id,blocked_id) VALUES (?,?)",
                (normal, target),
            ).lastrowid

        self.client.login_as(normal)
        csrf = self.client.db_csrf()
        attempts = [
            ("GET", "/admin", None),
            ("POST", f"/admin/user/{target}/toggle", {"csrf_token": csrf, "mb_level": "10"}),
            ("POST", f"/admin/product/{product}/delete", {"csrf_token": csrf, "role": "admin"}),
            ("POST", f"/admin/report/{report_id}/resolve", {"csrf_token": csrf}),
            ("POST", f"/admin/block/{block_id}/delete", {"csrf_token": csrf}),
        ]
        for method, path, data in attempts:
            status, _, _ = self.client.request(method, path, data)
            self.assertEqual(status, 403)

        forged = Client(self.app)
        forged.cookie = "tiny_session=forged-admin-token"
        status, _, _ = forged.request("GET", "/admin?mb_level=10&role=admin")
        self.assertEqual(status, 401)

        admin_client = Client(self.app)
        admin_client.login_as(admin)
        status, _, _ = admin_client.request("GET", "/admin")
        self.assertEqual(status, 200)
        with connect(self.app.config.database_path) as connection:
            connection.execute("UPDATE users SET role='user' WHERE id=?", (admin,))
        status, _, _ = admin_client.request("GET", "/admin")
        self.assertEqual(status, 403)

        with connect(self.app.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT status FROM users WHERE id=?", (target,)).fetchone()[0], "active")
            self.assertEqual(connection.execute("SELECT status FROM reports WHERE id=?", (report_id,)).fetchone()[0], "open")
            self.assertIsNotNone(connection.execute("SELECT 1 FROM user_blocks WHERE id=?", (block_id,)).fetchone())

    def test_direct_messages_are_private(self):
        alice = self.seed_user("alice")
        bob = self.seed_user("bob")
        eve = self.seed_user("eve")
        product = self.seed_product(alice)
        self.client.login_as(bob)
        csrf = self.client.db_csrf()
        status, _, _ = self.client.request("POST", "/chat/send", {"csrf_token": csrf, "product_id": str(product), "counterpart_id": str(alice), "body": "seller only secret"})
        self.assertEqual(status, 302)
        eve_client = Client(self.app)
        eve_client.login_as(eve)
        _, _, body = eve_client.request("GET", f"/chat/{product}")
        self.assertNotIn("seller only secret", body)

    def test_chat_photo_and_unread_notification(self):
        seller = self.seed_user("seller")
        buyer = self.seed_user("buyer")
        product = self.seed_product(seller)
        buyer_client = Client(self.app)
        buyer_client.login_as(buyer)
        png = png_bytes()
        status, _, _ = buyer_client.request(
            "POST", "/chat/send",
            {"csrf_token": buyer_client.db_csrf(), "product_id": str(product), "counterpart_id": str(seller), "body": ""},
            {"images": [("chat-1.png", "image/png", png), ("chat-2.png", "image/png", png)]},
        )
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            connection.execute("UPDATE products SET status='sold',buyer_id=? WHERE id=?", (buyer, product))
        seller_client = Client(self.app)
        seller_client.login_as(seller)
        _, _, inbox = seller_client.request("GET", "/chat")
        self.assertIn("안전한 자전거", inbox)
        self.assertIn("판매 완료", inbox)
        self.assertIn('<span class="notification-badge">1</span>', inbox)
        _, _, home = seller_client.request("GET", "/")
        self.assertIn("읽지 않은 메시지 1개", home)
        status, headers, unread = seller_client.request("GET", "/api/unread")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(unread, '{"unread":1}')
        _, _, chat = seller_client.request("GET", f"/chat/{product}/{buyer}")
        self.assertIn("chat-image", chat)
        self.assertIn("사진을 보냈습니다", chat)
        self.assertIn('data-max-files="10"', chat)
        self.assertRegex(chat, r'id="unread-badge"[^>]* hidden>0</span>')
        self.assertRegex(chat, r'id="notification-alert"[^>]* hidden>')
        _, _, home = seller_client.request("GET", "/")
        self.assertNotIn("읽지 않은 메시지 1개", home)
        _, _, unread = seller_client.request("GET", "/api/unread")
        self.assertEqual(unread, '{"unread":0}')
        with connect(self.app.config.database_path) as connection:
            message = connection.execute("SELECT image_filename,read_at FROM messages").fetchone()
            image_count = connection.execute("SELECT COUNT(*) FROM message_images").fetchone()[0]
        self.assertTrue(message["image_filename"].endswith(".png"))
        self.assertIsNotNone(message["read_at"])
        self.assertEqual(image_count, 2)
        status, headers, _ = seller_client.request("GET", f'/chat-uploads/{message["image_filename"]}')
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        outsider = Client(self.app)
        outsider.login_as(self.seed_user("outsider"))
        status, _, _ = outsider.request("GET", f'/chat-uploads/{message["image_filename"]}')
        self.assertEqual(status, 404)
        status, _, _ = outsider.request("GET", f'/uploads/{message["image_filename"]}')
        self.assertEqual(status, 404)

    def test_chat_report_requires_real_conversation_and_reaches_admin(self):
        seller = self.seed_user("seller")
        buyer = self.seed_user("buyer")
        outsider = self.seed_user("outsider")
        admin = self.seed_user("adminuser", role="admin")
        product = self.seed_product(seller, title="채팅 신고 확인 상품")
        buyer_client = Client(self.app)
        buyer_client.login_as(buyer)
        status, _, _ = buyer_client.request(
            "POST", "/chat/send",
            {
                "csrf_token": buyer_client.db_csrf(),
                "product_id": str(product),
                "counterpart_id": str(seller),
                "body": "거래 문의 메시지입니다",
            },
        )
        self.assertEqual(status, 302)
        status, _, chat = buyer_client.request("GET", f"/chat/{product}")
        self.assertEqual(status, 200)
        self.assertIn("이 채팅 신고하기", chat)
        status, _, report_form = buyer_client.request("GET", f"/report/chat/{product}/{seller}")
        self.assertEqual(status, 200)
        self.assertIn("채팅 신고 확인 상품", report_form)
        self.assertIn("nick_seller", report_form)
        status, _, _ = buyer_client.request(
            "POST", f"/report/chat/{product}/{seller}",
            {"csrf_token": buyer_client.db_csrf(), "reason": "채팅에서 부적절한 거래를 반복적으로 요구했습니다"},
        )
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            report = connection.execute(
                "SELECT target_type,target_id,reason FROM reports WHERE reporter_id=?",
                (buyer,),
            ).fetchone()
        self.assertEqual(report["target_type"], "user")
        self.assertEqual(report["target_id"], seller)
        self.assertIn("[채팅 신고", report["reason"])
        self.assertIn("채팅 신고 확인 상품", report["reason"])

        admin_client = Client(self.app)
        admin_client.login_as(admin)
        status, _, admin_page = admin_client.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("채팅 신고 확인 상품", admin_page)
        self.assertIn("부적절한 거래", admin_page)

        outsider_client = Client(self.app)
        outsider_client.login_as(outsider)
        status, _, _ = outsider_client.request("GET", f"/report/chat/{product}/{seller}")
        self.assertEqual(status, 404)
        status, _, _ = buyer_client.request(
            "POST", f"/report/chat/{product}/{seller}",
            {"csrf_token": buyer_client.db_csrf(), "reason": "같은 상대를 다시 신고하려는 충분한 사유입니다"},
        )
        self.assertEqual(status, 409)

    def test_user_block_hides_products_and_prevents_chat_and_purchase(self):
        seller = self.seed_user("seller")
        buyer = self.seed_user("buyer")
        product = self.seed_product(seller, title="차단 확인 상품")
        self.client.login_as(buyer)
        csrf = self.client.db_csrf()
        status, _, _ = self.client.request("POST", f"/block/{seller}/toggle", {"csrf_token": csrf})
        self.assertEqual(status, 302)
        _, _, home = self.client.request("GET", "/")
        self.assertNotIn("차단 확인 상품", home)
        status, _, _ = self.client.request("GET", f"/chat/{product}")
        self.assertEqual(status, 403)
        status, _, _ = self.client.request("POST", f"/products/{product}/purchase", {"csrf_token": csrf})
        self.assertEqual(status, 404)

    def test_admin_manages_reports_blocks_and_account_suspension(self):
        reporter = self.seed_user("reporter")
        target = self.seed_user("target")
        admin = self.seed_user("adminuser", role="admin")
        with connect(self.app.config.database_path) as connection:
            report_id = connection.execute("INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES (?,?,?,?)", (reporter, "user", target, "관리자 검토가 필요한 사용자 신고입니다")).lastrowid
            block_id = connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id) VALUES (?,?)", (reporter, target)).lastrowid
        self.client.login_as(admin)
        csrf = self.client.db_csrf()
        status, _, page = self.client.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("사용자·활동 정지", page)
        self.assertIn("사용자 차단 관계", page)
        self.assertIn("관리자 검토가 필요한", page)
        self.client.request("POST", f"/admin/report/{report_id}/resolve", {"csrf_token": csrf})
        self.client.request("POST", f"/admin/block/{block_id}/delete", {"csrf_token": csrf})
        self.client.request("POST", f"/admin/user/{target}/toggle", {"csrf_token": csrf})
        with connect(self.app.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT status FROM reports WHERE id=?", (report_id,)).fetchone()[0], "resolved")
            self.assertIsNone(connection.execute("SELECT 1 FROM user_blocks WHERE id=?", (block_id,)).fetchone())
            self.assertEqual(connection.execute("SELECT status FROM users WHERE id=?", (target,)).fetchone()[0], "suspended")

    def test_suspended_user_can_login_and_read_but_cannot_trade_or_send(self):
        suspended = self.seed_user("suspended", balance=100_000)
        seller = self.seed_user("seller")
        product = self.seed_product(seller, title="정지 계정 제한 상품", price=1_000)
        hidden_product = self.seed_product(suspended, title="정지 판매자의 숨김 상품", price=1_000)
        with connect(self.app.config.database_path) as connection:
            connection.execute(
                "UPDATE users SET password_hash=?,status='suspended' WHERE id=?",
                (hash_password("StrongPass123", iterations=100_000), suspended),
            )
            connection.execute(
                "INSERT INTO messages(sender_id,recipient_id,product_id,body) VALUES (?,?,?,?)",
                (seller, suspended, product, "정지 전에 받은 메시지"),
            )

        self.client.request("GET", "/login")
        status, headers, _ = self.client.request(
            "POST", "/login",
            {"csrf_token": self.client.db_csrf(), "username": "suspended", "password": "StrongPass123"},
        )
        self.assertEqual(status, 302)
        status, _, my_page = self.client.request("GET", "/my")
        self.assertEqual(status, 200)
        self.assertIn("계정 활동이 정지되었습니다", my_page)

        status, _, _ = self.client.request("GET", "/products/new")
        self.assertEqual(status, 403)
        status, _, _ = self.client.request(
            "POST", f"/products/{product}/purchase", {"csrf_token": self.client.db_csrf()}
        )
        self.assertEqual(status, 403)
        status, _, _ = self.client.request(
            "POST", "/chat/send",
            {"csrf_token": self.client.db_csrf(), "product_id": str(product), "counterpart_id": str(seller), "body": "보내면 안 됨"},
        )
        self.assertEqual(status, 403)

        status, _, chat = self.client.request("GET", f"/chat/{product}")
        self.assertEqual(status, 200)
        self.assertIn("정지 전에 받은 메시지", chat)
        self.assertIn("새 메시지는 보낼 수 없습니다", chat)
        with connect(self.app.config.database_path) as connection:
            self.assertIsNotNone(connection.execute("SELECT read_at FROM messages").fetchone()[0])
        anonymous = Client(self.app)
        _, _, home = anonymous.request("GET", "/")
        self.assertNotIn("정지 판매자의 숨김 상품", home)
        self.assertNotIn(f'/products/{hidden_product}', home)

    def test_withdrawal_anonymizes_account_revokes_sessions_and_hides_products(self):
        member = self.seed_user("leaving")
        admin = self.seed_user("adminuser", role="admin")
        product = self.seed_product(member, title="탈퇴 전 판매 상품")
        password = "StrongPass123"
        image_filename = "a" * 32 + ".png"
        (self.app.upload_dir / image_filename).write_bytes(png_bytes())
        with connect(self.app.config.database_path) as connection:
            connection.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(password, iterations=100_000), member),
            )
            connection.execute("UPDATE products SET image_filename=? WHERE id=?", (image_filename, product))
            connection.execute(
                "INSERT INTO product_images(product_id,filename,position) VALUES (?,?,0)",
                (product, image_filename),
            )

        self.client.login_as(member)
        another_device = Client(self.app)
        another_device.login_as(member)
        status, _, _ = Client(self.app).request("GET", f"/uploads/{image_filename}")
        self.assertEqual(status, 200)
        status, _, page = self.client.request("GET", "/account/withdraw")
        self.assertEqual(status, 200)
        self.assertIn("회원탈퇴", page)

        status, _, page = self.client.request(
            "POST", "/account/withdraw",
            {
                "csrf_token": self.client.db_csrf(),
                "current_password": "WrongPassword123",
                "confirmation": "회원탈퇴",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("현재 비밀번호가 올바르지 않습니다", page)

        status, headers, _ = self.client.request(
            "POST", "/account/withdraw",
            {
                "csrf_token": self.client.db_csrf(),
                "current_password": password,
                "confirmation": "회원탈퇴",
            },
        )
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/")
        self.assertEqual(self.client.cookie, "tiny_session=")

        with connect(self.app.config.database_path) as connection:
            withdrawn = connection.execute(
                "SELECT username,nickname,password_hash,bio,status,deleted_at FROM users WHERE id=?",
                (member,),
            ).fetchone()
            self.assertNotEqual(withdrawn["username"], "leaving")
            self.assertTrue(withdrawn["nickname"].startswith("탈퇴한회원-"))
            self.assertFalse(verify_password(password, withdrawn["password_hash"]))
            self.assertEqual(withdrawn["bio"], "")
            self.assertEqual(withdrawn["status"], "suspended")
            self.assertIsNotNone(withdrawn["deleted_at"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (member,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT moderation_status FROM products WHERE id=?", (product,)).fetchone()[0], "hidden")

        status, _, _ = another_device.request("GET", "/my")
        self.assertEqual(status, 401)
        status, _, product_page = Client(self.app).request("GET", f"/products/{product}")
        self.assertEqual(status, 404)
        self.assertIn("탈퇴한 회원입니다", product_page)
        status, _, _ = Client(self.app).request("GET", f"/uploads/{image_filename}")
        self.assertEqual(status, 404)
        status, _, profile_page = Client(self.app).request("GET", f"/users/{member}")
        self.assertEqual(status, 404)
        self.assertIn("탈퇴한 회원입니다", profile_page)
        _, _, home = Client(self.app).request("GET", "/")
        self.assertNotIn("탈퇴 전 판매 상품", home)

        login_client = Client(self.app)
        login_client.request("GET", "/login")
        status, _, _ = login_client.request(
            "POST", "/login",
            {"csrf_token": login_client.db_csrf(), "username": "leaving", "password": password},
        )
        self.assertEqual(status, 401)

        admin_client = Client(self.app)
        admin_client.login_as(admin)
        status, _, admin_page = admin_client.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("탈퇴 회원", admin_page)
        status, _, _ = admin_client.request(
            "POST", f"/admin/user/{member}/toggle",
            {"csrf_token": admin_client.db_csrf()},
        )
        self.assertEqual(status, 409)

    def test_nickname_is_unique_case_insensitively(self):
        with connect(self.app.config.database_path) as connection:
            connection.execute("INSERT INTO users(username,nickname,password_hash) VALUES (?,?,?)", ("first", "MarketNick", "x"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO users(username,nickname,password_hash) VALUES (?,?,?)", ("second", "marketnick", "x"))

    def test_registration_saves_public_nickname(self):
        self.client.request("GET", "/register")
        status, _, _ = self.client.request("POST", "/register", {"csrf_token": self.client.db_csrf(), "username": "login_only", "nickname": "공개닉네임", "password": "StrongPass123"})
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            user = connection.execute("SELECT username,nickname FROM users WHERE username=?", ("login_only",)).fetchone()
        self.assertEqual(user["nickname"], "공개닉네임")

    def test_valid_png_upload_is_saved_and_fake_image_rejected(self):
        seller = self.seed_user("seller")
        self.client.login_as(seller)
        csrf = self.client.db_csrf()
        form = {"csrf_token": csrf, "title": "사진 상품", "price": "1000", "category": "other", "item_condition": "good", "description": "사진이 포함된 안전한 상품입니다."}
        png = png_bytes(metadata=True)
        status, _, _ = self.client.request("POST", "/products/new", form, {"image": ("item.png", "image/png", png)})
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            filename = connection.execute("SELECT image_filename FROM products").fetchone()[0]
        self.assertTrue((self.app.upload_dir / filename).is_file())
        status, headers, image_body = self.client.request("GET", f"/uploads/{filename}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        status, _, body = self.client.request("POST", "/products/new", form, {"image": ("fake.png", "image/png", b"not an image")})
        self.assertEqual(status, 400)
        self.assertIn("실제 PNG/JPEG 형식이 일치해야 합니다", body)
        with (self.app.upload_dir / filename).open("rb") as stored:
            self.assertNotIn(b"PRIVATE-METADATA-MARKER", stored.read())

    def test_webp_upload_is_rejected(self):
        seller = self.seed_user("webpseller")
        self.client.login_as(seller)
        form = {"csrf_token": self.client.db_csrf(), "title": "웹피 사진 상품", "price": "1000", "category": "other", "item_condition": "good", "description": "WebP 사진 업로드를 확인하는 상품입니다."}
        webp = base64.b64decode("UklGRiYAAABXRUJQVlA4IBoAAAAwAQCdASoBAAEAAgA0JZwAA3AA/vpgKj8AAA==")
        status, _, _ = self.client.request("POST", "/products/new", form, {"image": ("item.webp", "image/webp", webp)})
        self.assertEqual(status, 400)

    def test_disguised_and_malformed_webp_is_rejected(self):
        width_minus_one = (1200 - 1).to_bytes(3, "little")
        height_minus_one = (1200 - 1).to_bytes(3, "little")
        vp8x = b"RIFF" + (26).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + b"\x00\x00\x00\x00" + width_minus_one + height_minus_one + b"\x00" * 4
        with self.assertRaises(ValueError):
            validate_image(UploadedFile("disguised.png", "image/png", vp8x))

    def test_product_accepts_ten_photos_and_rejects_eleven(self):
        seller = self.seed_user("multiseller")
        self.client.login_as(seller)
        form = {"csrf_token": self.client.db_csrf(), "title": "사진 열 장 상품", "price": "1000", "category": "other", "item_condition": "good", "description": "여러 장의 상품 사진을 확인하는 상품입니다."}
        png = png_bytes()
        _, _, form_page = self.client.request("GET", "/products/new")
        self.assertIn('data-max-files="10"', form_page)
        self.assertIn("최대 10장까지만 선택", form_page)
        ten = [(f"item-{index}.png", "image/png", png) for index in range(10)]
        status, headers, _ = self.client.request("POST", "/products/new", form, {"images": ten})
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            product_id = connection.execute("SELECT id FROM products").fetchone()[0]
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM product_images WHERE product_id=?", (product_id,)).fetchone()[0], 10)
        status, _, detail = self.client.request("GET", f"/products/{product_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail.count('class="detail-image"'), 10)

        form["csrf_token"] = self.client.db_csrf()
        eleven = [(f"too-many-{index}.png", "image/png", png) for index in range(11)]
        status, _, body = self.client.request("POST", "/products/new", form, {"images": eleven})
        self.assertEqual(status, 400)
        self.assertIn("최대 10장", body)

    def test_new_product_requires_a_photo(self):
        seller = self.seed_user("seller")
        self.client.login_as(seller)
        form = {"csrf_token": self.client.db_csrf(), "title": "사진 없는 상품", "price": "1000", "category": "other", "item_condition": "good", "description": "사진 선택 여부를 확인하는 상품입니다."}
        status, _, body = self.client.request("POST", "/products/new", form)
        self.assertEqual(status, 400)
        self.assertIn("상품 사진을 선택", body)

    def test_admin_can_delete_only_reported_product(self):
        seller = self.seed_user("seller")
        reporter = self.seed_user("reporter")
        admin = self.seed_user("adminuser", role="admin")
        reported = self.seed_product(seller, title="신고 상품")
        clean = self.seed_product(seller, title="정상 상품")
        with connect(self.app.config.database_path) as connection:
            connection.execute("INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES (?,?,?,?)", (reporter, "product", reported, "관리자 확인이 필요한 신고 사유입니다"))
        self.client.login_as(admin)
        csrf = self.client.db_csrf()
        status, _, _ = self.client.request("POST", f"/admin/product/{clean}/delete", {"csrf_token": csrf})
        self.assertEqual(status, 403)
        status, _, _ = self.client.request("POST", f"/admin/product/{reported}/delete", {"csrf_token": csrf})
        self.assertEqual(status, 302)
        with connect(self.app.config.database_path) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM products WHERE id=?", (reported,)).fetchone())
            state = connection.execute("SELECT status FROM reports WHERE target_id=?", (reported,)).fetchone()[0]
        self.assertEqual(state, "resolved")


if __name__ == "__main__":
    unittest.main()
