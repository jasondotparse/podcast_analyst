from http.cookies import SimpleCookie
import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.backend.chat import ChatHarness
from src.backend.main import app, events
from starlette.requests import Request


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.state.app_password = "test-password-only"
        app.state.public_origin = "https://testserver"
        app.state.login_sessions = {}
        app.state.harness = ChatHarness(Path('/tmp'))
        app.state.harness.client.ready = True
        app.state.harness.client.request = AsyncMock(return_value={"thread": {"id": "thread-one"}})

    async def request(self, method, path, body=None, cookie=None, origin="https://testserver"):
        headers = [(b"host", b"testserver"), (b"content-type", b"application/json")]
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        if origin:
            headers.append((b"origin", origin.encode()))
        scope = {"type": "http", "http_version": "1.1", "method": method, "path": path,
                 "raw_path": path.encode(), "query_string": b"", "headers": headers,
                 "scheme": "https", "server": ("testserver", 443), "client": ("127.0.0.1", 10000)}
        sent = []
        async def receive():
            return {"type": "http.request", "body": json.dumps(body).encode() if body else b""}
        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent[0], b"".join(item.get("body", b"") for item in sent[1:])

    async def login(self):
        response, _ = await self.request("POST", "/api/login", {"password": "test-password-only"})
        self.assertEqual(response["status"], 200)
        cookie = SimpleCookie()
        cookie.load(dict(response["headers"])[b"set-cookie"].decode())
        self.assertTrue(cookie["podcast_session"]["secure"])
        self.assertTrue(cookie["podcast_session"]["httponly"])
        self.assertEqual(cookie["podcast_session"]["samesite"], "lax")
        return "podcast_session=" + cookie["podcast_session"].value

    async def test_anonymous_ui_api_sse_and_wrong_password_are_blocked(self):
        for method, path, status in (("GET", "/app/", 303), ("POST", "/api/sessions", 401),
                                     ("GET", "/api/sessions/unknown/events", 401), ("GET", "/ready", 401)):
            response, _ = await self.request(method, path)
            self.assertEqual(response["status"], status, path)
        response, _ = await self.request("POST", "/api/login", {"password": "wrong"})
        self.assertEqual(response["status"], 401)
        self.assertEqual((await self.request("GET", "/health"))[0]["status"], 200)

    async def test_logout_and_expiry_revoke_server_side_access(self):
        cookie = await self.login()
        self.assertEqual((await self.request("GET", "/ready", cookie=cookie))[0]["status"], 200)
        self.assertEqual((await self.request("POST", "/api/logout", cookie=cookie))[0]["status"], 303)
        self.assertEqual((await self.request("GET", "/ready", cookie=cookie))[0]["status"], 401)
        cookie = await self.login()
        app.state.login_sessions[cookie.split("=", 1)[1]] = time.time() - 1
        self.assertEqual((await self.request("GET", "/ready", cookie=cookie))[0]["status"], 401)

    async def test_other_logged_in_browser_cannot_read_or_write_chat(self):
        first, second = await self.login(), await self.login()
        response, body = await self.request("POST", "/api/sessions", cookie=first)
        self.assertEqual(response["status"], 201)
        session_id = json.loads(body)["session_id"]
        for method, path, body in (("GET", f"/api/sessions/{session_id}/events", None),
                                   ("POST", f"/api/sessions/{session_id}/turns", {"message": "hello"})):
            self.assertEqual((await self.request(method, path, body, second))[0]["status"], 404)

    async def test_cross_site_login_and_logout_are_rejected(self):
        cookie = await self.login()
        for path, body in (("/api/login", {"password": "test-password-only"}), ("/api/logout", None)):
            response, _ = await self.request("POST", path, body, cookie, "https://other.example")
            self.assertEqual(response["status"], 403)

    async def test_open_event_stream_stops_after_logout(self):
        cookie = await self.login()
        login_id = cookie.split("=", 1)[1]
        session_id = await app.state.harness.create_session(login_id)
        async def receive():
            return {"type": "http.request", "body": b""}
        request = Request({"type": "http", "app": app, "headers": [],
                           "state": {"login_id": login_id}}, receive)
        response = await events(session_id, request)
        iterator = response.body_iterator
        self.assertIn("event: ready", await anext(iterator))
        app.state.login_sessions.clear()
        self.assertIn("Session expired", await anext(iterator))
        await iterator.aclose()


if __name__ == '__main__':
    unittest.main()
