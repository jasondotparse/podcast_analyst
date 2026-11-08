import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from src.backend.chat import ChatHarness, ChatSession
from src.backend.clients.codex_client import CodexAppServerClient, CodexError


class RpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.disconnections = []
        self.client = CodexAppServerClient(lambda *_: None, self.disconnections.append)
        self.reader = asyncio.StreamReader()
        self.client.process = SimpleNamespace(stdout=self.reader)
        self.client.ready = True
        self.client.write = AsyncMock()
        self.read_task = asyncio.create_task(self.client.read_stdout())

    async def asyncTearDown(self):
        self.read_task.cancel()
        for task in list(self.client.tasks):
            task.cancel()
        await asyncio.gather(self.read_task, *list(self.client.tasks), return_exceptions=True)

    def feed(self, message):
        self.reader.feed_data((json.dumps(message) + '\n').encode())

    async def test_out_of_order_responses_match_their_request(self):
        first = asyncio.create_task(self.client.request('first', {}))
        second = asyncio.create_task(self.client.request('second', {}))
        await asyncio.sleep(0)
        self.feed({'id': 2, 'result': {'answer': 'second'}})
        self.feed({'id': 1, 'result': {'answer': 'first'}})
        self.assertEqual(await first, {'answer': 'first'})
        self.assertEqual(await second, {'answer': 'second'})
        self.assertEqual(self.client.pending, {})

    async def test_server_request_does_not_block_rpc_responses(self):
        release = asyncio.Event()

        async def slow_writer(message):
            if message.get('id') == 99:
                await release.wait()

        self.client.write.side_effect = slow_writer
        request = asyncio.create_task(self.client.request('model/list', {}))
        await asyncio.sleep(0)
        self.feed({'id': 99, 'method': 'item/tool/call', 'params': {}})
        self.feed({'id': 1, 'result': {'data': []}})
        self.assertEqual(await asyncio.wait_for(request, .5), {'data': []})
        release.set()
        await asyncio.sleep(0)
        replies = [call.args[0] for call in self.client.write.call_args_list]
        self.assertTrue(any(reply.get('id') == 99 and 'error' in reply for reply in replies))

    async def test_crash_fails_pending_requests_and_readiness(self):
        request = asyncio.create_task(self.client.request('thread/start', {}))
        await asyncio.sleep(0)
        self.reader.feed_eof()
        with self.assertRaises(CodexError):
            await request
        self.assertFalse(self.client.ready)
        self.assertEqual(len(self.disconnections), 1)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.harness = ChatHarness(Path('/tmp'))
        self.harness.client.ready = True
        for name in ('one', 'two'):
            session = ChatSession(thread_id=f'thread-{name}')
            self.harness.sessions[name] = session
            self.harness.by_thread[session.thread_id] = session

    async def test_early_completion_is_buffered_and_does_not_resurrect_busy(self):
        async def reply_after_notifications(method, params):
            self.harness.notification('item/agentMessage/delta', {
                'threadId': 'thread-one', 'itemId': 'message-1', 'delta': 'Hi',
            })
            self.harness.notification('turn/completed', {
                'threadId': 'thread-one', 'turn': {'id': 'turn-1', 'status': 'completed'},
            })
            return {'turn': {'id': 'turn-1'}}

        self.harness.client.request = AsyncMock(side_effect=reply_after_notifications)
        await self.harness.start_turn('one', 'hello')
        session = self.harness.sessions['one']
        self.assertFalse(session.busy)
        self.assertIsNone(session.active_turn_id)
        self.assertEqual([event[1] for event in session.events], ['delta', 'completed'])
        self.assertEqual(len(self.harness.sessions['two'].events), 0)

    async def test_same_thread_rejected_while_other_threads_can_run(self):
        release = asyncio.Event()

        async def wait_for_first(method, params):
            if params['threadId'] == 'thread-one':
                await release.wait()
            return {'turn': {'id': params['threadId'] + '-turn'}}

        self.harness.client.request = AsyncMock(side_effect=wait_for_first)
        first = asyncio.create_task(self.harness.start_turn('one', 'hello'))
        await asyncio.sleep(0)
        with self.assertRaises(HTTPException) as error:
            await self.harness.start_turn('one', 'another')
        self.assertEqual(error.exception.status_code, 409)
        self.assertTrue((await self.harness.start_turn('two', 'hello'))['accepted'])
        release.set()
        await first

    async def test_terminal_outcomes_release_only_the_right_thread(self):
        for status in ('completed', 'failed', 'interrupted'):
            for session in self.harness.sessions.values():
                session.busy = True
            self.harness.notification('turn/completed', {
                'threadId': 'thread-one', 'turn': {'id': 'turn-1', 'status': status},
            })
            self.assertFalse(self.harness.sessions['one'].busy)
            self.assertTrue(self.harness.sessions['two'].busy)

    async def test_event_history_is_bounded(self):
        session = self.harness.sessions['one']
        for index in range(600):
            session.emit('delta', {'text': str(index)})
        self.assertEqual(len(session.events), 512)
        self.assertEqual(session.sequence, 600)


if __name__ == '__main__':
    unittest.main()
