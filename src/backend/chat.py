"""Session routing and a small bounded SSE event log; Codex owns conversation history."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from .clients.codex_client import CodexAppServerClient, CodexError


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


CHAT_INSTRUCTIONS = (
    "You are the Podcast Analyst research assistant. Answer from the ingested podcast data using your tools: "
    "vector_search finds relevant transcript passages; postgres_database_query reads claims, episodes, and "
    "metadata; graph_query follows links between episodes, passages, claims, entities, and theses; "
    "add_thesis saves the user's investment thesis; register_rss_to_watcher starts watching a podcast's feed "
    "for new episodes. Every claim carries a signal score (claims.signal, also "
    "Claim.signal in the graph) from 0 to 100: the mean of specificity (how checkable it is), materiality (how "
    "much it would move an analyst's view of a public company), and source_standing (how well placed the speaker "
    "is to know, and whether it is first-hand), discounted when no speaker is attributed. When evidence is "
    "plentiful, lead with the highest-signal claims and say which are low-signal; when it is scarce, use what "
    "there is and say so. When a high-signal claim matters to the user's question, you may run a quick web search "
    "to check whether it has since been confirmed, contradicted, or reported elsewhere, and report what you found "
    "separately from the podcast evidence; the podcast claim stays attributed to its speaker either way. Back each "
    "finding with the podcast, episode, speaker, and a verbatim quote, plus the episode URL when there is one. If "
    "the data holds no evidence, say so. Do not inspect local files or run commands."
)


class ChatHarness:
    def __init__(self, workspace: Path, model: str | None = None, tools: list[dict] | None = None,
                 call_tool=None):
        self.workspace = workspace
        self.model = model
        # Dynamic tools advertised to every chat thread, and the handler that runs them.
        self.tools = tools or []
        self.chat_tool_handler = call_tool
        self.sessions: dict[str, ChatSession] = {}
        self.by_thread: dict[str, ChatSession] = {}
        # Background (pipeline) threads: thread ID -> (turn-completed future, tool handler).
        self.background: dict[str, tuple[asyncio.Future, object]] = {}
        self.client = CodexAppServerClient(self.notification, self.disconnected, self.server_request)

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
            "dynamicTools": self.tools,
            "developerInstructions": CHAT_INSTRUCTIONS,
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

    async def run_task(self, text: str, skill: Path, tools: list, call_tool, timeout: float) -> str:
        """Run one turn on a fresh thread with its own dynamic tools; return the thread ID."""
        self.require_ready()
        params = {
            "cwd": str(self.workspace), "approvalPolicy": "never", "sandbox": "read-only",
            "dynamicTools": tools, "config": {"features": {"memories": False}},
        }
        if self.model:
            params["model"] = self.model
        thread_id = (await self.client.request("thread/start", params))["thread"]["id"]
        done = asyncio.get_running_loop().create_future()
        self.background[thread_id] = (done, call_tool)
        try:
            turn = await self.client.request("turn/start", {"threadId": thread_id, "input": [
                {"type": "skill", "name": skill.parent.name, "path": str(skill)},
                {"type": "text", "text": text},
            ]})
            try:
                result = await asyncio.wait_for(done, timeout)
            except TimeoutError:
                await self.client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn["turn"]["id"]})
                raise TimeoutError(f"Codex thread {thread_id} timed out after {timeout:.0f}s")
        finally:
            self.background.pop(thread_id, None)
        if result["status"] != "completed":
            raise CodexError(f"Codex thread {thread_id} ended {result['status']}: {result.get('error')}")
        return thread_id

    def server_request(self, method: str, params: dict):
        if method != "item/tool/call":
            return None
        task = self.background.get(params.get("threadId"))
        if task:
            return self.call_tool(task[1], params)
        if params.get("threadId") in self.by_thread and self.chat_tool_handler:
            return self.call_tool(self.chat_tool_handler, params)
        return None

    async def call_tool(self, handler, params: dict) -> dict:
        try:
            text, success = await handler(params["tool"], params["arguments"]), True
        except Exception as error:  # The model sees the error and can correct itself.
            text, success = f"Error: {error}", False
        return {"contentItems": [{"type": "inputText", "text": text}], "success": success}

    def notification(self, method: str, params: dict):
        task = self.background.get(params.get("threadId"))
        if task and method == "turn/completed" and not task[0].done():
            task[0].set_result(params["turn"])
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
        for done, _ in self.background.values():
            if not done.done():
                done.set_exception(CodexError(message))
        for session in self.sessions.values():
            session.invalid = True
            session.busy = False
            session.emit("fatal", {"message": message})
