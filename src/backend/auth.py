"""Shared-password login with opaque cookies and single-worker server-side sessions."""

import hmac
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field


COOKIE = "podcast_session"
SESSION_SECONDS = 12 * 60 * 60
router = APIRouter()

LOGIN_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Sign in · Podcast Analyst</title>
<style>body{font:18px system-ui;max-width:420px;margin:12vh auto;padding:24px}
input,button{font:inherit;padding:10px;box-sizing:border-box;width:100%;margin:8px 0}
#error{color:#b42318}</style>
<h1>Podcast Analyst</h1><p>Enter the password provided to you.</p>
<form id="login"><label for="password">Password</label>
<input id="password" type="password" autocomplete="current-password" required autofocus>
<button>Sign in</button><p id="error" role="alert"></p></form>
<script>document.querySelector('#login').onsubmit=async(event)=>{
event.preventDefault();const error=document.querySelector('#error');
try{const response=await fetch('/api/login',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:document.querySelector('#password').value})});
if(response.ok){location.href='/app/';return}
error.textContent='Incorrect password. Try again.';
}catch{error.textContent='Unable to sign in. Please try again.'}};</script></html>"""


class PasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def valid_login(state, session_id: str | None) -> bool:
    return state.login_sessions.get(session_id, 0) > time.time()


class PasswordAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        scope.setdefault("state", {})["login_id"] = None
        state = request.app.state
        if not state.app_password:
            return await self.app(scope, receive, send)

        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = state.public_origin or str(request.base_url).rstrip("/")
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return await JSONResponse({"detail": "Cross-site request rejected."}, status_code=403)(scope, receive, send)

        public = request.url.path in ("/login", "/api/login", "/health")
        if not public:
            session_id = request.cookies.get(COOKIE)
            if not valid_login(state, session_id):
                if request.url.path.startswith("/api/") or request.url.path == "/ready":
                    response = JSONResponse({"detail": "Sign in required."}, status_code=401)
                else:
                    response = RedirectResponse("/login", status_code=303)
                return await response(scope, receive, send)
            request.state.login_id = session_id
        await self.app(scope, receive, send)


@router.get("/login")
async def login_page():
    return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})


@router.post("/api/login")
async def login(body: PasswordInput, request: Request):
    state = request.app.state
    if not state.app_password or not hmac.compare_digest(body.password.encode(), state.app_password.encode()):
        raise HTTPException(401, "Incorrect password.")
    now = time.time()
    state.login_sessions = {key: expiry for key, expiry in state.login_sessions.items() if expiry > now}
    state.login_sessions.pop(request.cookies.get(COOKIE), None)
    session_id = secrets.token_urlsafe(32)
    state.login_sessions[session_id] = now + SESSION_SECONDS
    response = JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, session_id, max_age=SESSION_SECONDS, httponly=True,
                        secure=bool(state.public_origin and state.public_origin.startswith("https://")), samesite="lax")
    return response


@router.post("/api/logout")
async def logout(request: Request):
    request.app.state.login_sessions.pop(request.cookies.get(COOKIE), None)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response
