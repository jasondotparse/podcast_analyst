"""Session routing and a small bounded SSE event log; Codex owns conversation history."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from .codex_client import CodexAppServerClient, CodexError


log = logging.getLogger(__name__)


@dataclass
class ChatSession:
    thread_id: str
    owner_session_id: str | None = None
    busy: bool = False
    active_turn_id: str | None = None
    invalid: bool = False
    streaming: bool = False
    sequence: int = 0
    events: deque = field(default_factory=lambda: deque(maxlen=512))
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, event: str, data: dict):
        self.sequence += 1
        self.events.append((self.sequence, event, data))
        self.changed.set()


class ChatHarness:
    def __init__(self, workspace: Path, model: str | None = None):
        self.workspace = workspace
        self.model = model
        self.sessions: dict[str, ChatSession] = {}
        self.by_thread: dict[str, ChatSession] = {}
        self.client = CodexAppServerClient(self.notification, self.disconnected)

    def require_ready(self):
        if not self.client.ready:
            raise HTTPException(503, "Codex is not ready. Check the backend and reload the page.")

    def session(self, session_id: str, owner_session_id: str | None = None) -> ChatSession:
        self.require_ready()
        session = self.sessions.get(session_id)
        if session is None or session.owner_session_id != owner_session_id:
            raise HTTPException(404, "Unknown chat session. Reload the page to start a new chat.")
        if session.invalid:
            raise HTTPException(503, "This chat connection was lost. Reload the page to start a new chat.")
        return session

    async def create_session(self, owner_session_id: str | None = None) -> str:
        self.require_ready()
        params = {
            "cwd": str(self.workspace), "approvalPolicy": "never", "sandbox": "read-only",
            "dynamicTools": [],
            "developerInstructions": "You are the Podcast Analyst chat assistant. Respond to the user's messages. No podcast data or research tools are connected yet. Do not inspect local files or run commands.",
        }
        if self.model:
            params["model"] = self.model
        result = await self.client.request("thread/start", params)
        session_id = str(uuid4())
        session = ChatSession(thread_id=result["thread"]["id"], owner_session_id=owner_session_id)
        self.sessions[session_id] = session
        self.by_thread[session.thread_id] = session
        return session_id

    async def start_turn(self, session_id: str, text: str, owner_session_id: str | None = None):
        session = self.session(session_id, owner_session_id)
        # There is no await between admission and claiming busy, so this is atomic
        # on our one asyncio event loop, including concurrent HTTP requests.
        if session.busy:
            raise HTTPException(409, "This chat already has an active turn.")
        session.busy = True
        try:
            result = await self.client.request("turn/start", {
                "threadId": session.thread_id, "input": [{"type": "text", "text": text}],
            })
            # Notifications can finish the turn before the RPC response arrives.
            if session.busy and session.active_turn_id is None:
                session.active_turn_id = result["turn"]["id"]
            return {"accepted": True}
        except CodexError:
            # Acceptance may be ambiguous after a timeout; never replay the turn.
            session.invalid = True
            session.busy = False
            session.emit("fatal", {"message": "The Codex request failed. Reload the page to start a new chat."})
            raise

    def notification(self, method: str, params: dict):
        session = self.by_thread.get(params.get("threadId"))
        if session is None:
            return
        if method == "turn/started":
            session.active_turn_id = params["turn"]["id"]
            session.emit("started", {})
        elif method == "item/agentMessage/delta":
            session.emit("delta", {"item_id": params["itemId"], "text": params["delta"]})
        elif method == "item/completed" and params["item"]["type"] == "agentMessage":
            item = params["item"]
            session.emit("message", {"item_id": item["id"], "text": item["text"]})
        elif method == "turn/completed":
            turn = params["turn"]
            if turn.get("error"):
                detail = str(turn["error"]).replace(self.client.api_key, "[redacted]") if self.client.api_key else str(turn["error"])
                log.warning("Codex turn %s failed: %s", turn["id"], detail)
            session.busy = False
            session.active_turn_id = None
            session.emit("completed", {
                "status": turn["status"],
                "error": "Codex could not complete this turn." if turn.get("error") else None,
            })

    def disconnected(self, message: str):
        for session in self.sessions.values():
            session.invalid = True
            session.busy = False
            session.emit("fatal", {"message": message})
