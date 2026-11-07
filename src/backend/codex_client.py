"""One JSON-lines connection to a long-lived Codex app-server process."""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path


log = logging.getLogger(__name__)


class CodexError(RuntimeError):
    pass


class CodexAppServerClient:
    def __init__(self, on_notification: Callable, on_disconnect: Callable):
        self.on_notification = on_notification
        self.on_disconnect = on_disconnect
        self.process = None
        self.pending = {}
        self.write_lock = asyncio.Lock()
        self.next_id = 0
        self.tasks = set()
        self.ready = False
        self.api_key = ""

    def spawn_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def start(self, api_key: str, home: Path, workspace: Path):
        self.api_key = api_key
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "OPENAI_API_KEY": api_key, "CODEX_HOME": str(home)}
        self.process = await asyncio.create_subprocess_exec(
            os.environ.get("CODEX_BIN", "codex"), "app-server", "--stdio",
            "--disable", "shell_tool", "--disable", "multi_agent",
            "-c", 'web_search="disabled"',
            cwd=workspace, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024,
        )
        self.spawn_task(self.read_stdout())
        self.spawn_task(self.drain_stderr())
        try:
            await self.request("initialize", {
                "clientInfo": {"name": "podcast_analyst", "title": "Podcast Analyst", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            await self.write({"method": "initialized", "params": {}})
            # Explicit API-key auth; never reuse the developer's ChatGPT login.
            await self.request("account/login/start", {"type": "apiKey", "apiKey": api_key})
            self.ready = True
        except BaseException:
            await self.close()
            raise

    async def write(self, message):
        async with self.write_lock:
            if self.process is None or self.process.returncode is not None:
                raise CodexError("Codex is not running. Restart the backend.")
            try:
                self.process.stdin.write((json.dumps(message) + "\n").encode())
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise CodexError("Codex disconnected. Restart the backend.") from error

    async def request(self, method: str, params: dict, timeout: float = 30):
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            raise CodexError(f"Codex request timed out: {method}. Start a new chat.") from error
        finally:
            self.pending.pop(request_id, None)

    async def read_stdout(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        self.spawn_task(self.decline_request(message))
                    else:
                        self.on_notification(message["method"], message.get("params", {}))
                else:
                    future = self.pending.get(message.get("id"))
                    if future is not None and not future.done():
                        if "error" in message:
                            detail = str(message["error"].get("message", "RPC failed"))
                            future.set_exception(CodexError(detail.replace(self.api_key, "[redacted]") if self.api_key else detail))
                        else:
                            future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Codex stdout connection failed")
        finally:
            self.ready = False
            error = CodexError("Codex disconnected. Restart the backend and reload the page.")
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            self.on_disconnect(str(error))

    async def decline_request(self, message):
        # Dynamic tools and approvals are not part of this local chat slice.
        try:
            await self.write({"id": message["id"], "error": {
                "code": -32601, "message": "This chat client does not support server requests.",
            }})
        except CodexError:
            pass

    async def drain_stderr(self):
        while line := await self.process.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            log.info("Codex: %s", text.replace(self.api_key, "[redacted]"))

    async def close(self):
        self.ready = False
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
