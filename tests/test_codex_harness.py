import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

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

    async def test_chat_graph_preview_and_background_isolation(self):
        thesis, claim = str(uuid4()), str(uuid4())
        payload = {'nodes': [{'id': thesis}, {'id': claim}],
                   'links': [{'source': claim, 'target': thesis, 'type': 'CHALLENGES'}], 'truncated': False}
        self.harness.chat_tool_handler = AsyncMock(return_value=json.dumps({'id': claim}))
        self.harness.graph_view = SimpleNamespace(subgraph=AsyncMock(return_value=payload))
        params = {'tool': 'graph_query', 'threadId': 'thread-one', 'callId': 'call-1',
                  'arguments': {'cypher': f"MATCH (t:Thesis {{id: '{thesis.upper()}'}}) RETURN t"}}
        result = await self.harness.server_request('item/tool/call', params)
        self.assertTrue(result['success'])
        self.harness.graph_view.subgraph.assert_awaited_once_with([thesis, claim])
        self.assertEqual(list(self.harness.sessions['one'].events)[0][1:], ('graph', {'call_id': 'call-1', **payload}))
        self.assertFalse(self.harness.sessions['two'].events)

        self.harness.graph_view.subgraph.reset_mock()
        self.harness.background['background'] = (asyncio.get_running_loop().create_future(), self.harness.chat_tool_handler)
        result = await self.harness.server_request('item/tool/call', {**params, 'threadId': 'background'})
        self.assertTrue(result['success'])
        self.harness.graph_view.subgraph.assert_not_awaited()

    async def test_preview_failure_empty_result_and_failed_tool_do_not_emit(self):
        self.harness.chat_tool_handler = AsyncMock(return_value='[]')
        self.harness.graph_view = SimpleNamespace(subgraph=AsyncMock(return_value={'nodes': [], 'links': []}))
        params = {'tool': 'graph_query', 'threadId': 'thread-one', 'callId': 'call-1', 'arguments': {'cypher': 'RETURN 1'}}
        self.assertTrue((await self.harness.server_request('item/tool/call', params))['success'])
        self.harness.graph_view.subgraph.side_effect = RuntimeError('preview broke')
        with self.assertLogs('src.backend.chat', level='WARNING') as logs:
            result = await self.harness.server_request('item/tool/call', params)
        self.assertTrue(result['success'])
        self.assertEqual(result['contentItems'][0]['text'], '[]')
        self.assertIn('Graph preview failed', logs.output[0])
        self.harness.graph_view.subgraph.reset_mock()
        self.harness.chat_tool_handler.side_effect = ValueError('query broke')
        self.assertFalse((await self.harness.server_request('item/tool/call', params))['success'])
        self.harness.chat_tool_handler.side_effect = None
        await self.harness.server_request('item/tool/call', {**params, 'tool': 'postgres_database_query'})
        self.harness.graph_view.subgraph.assert_not_awaited()
        self.assertFalse(self.harness.sessions['one'].events)


class ToolActivityTests(unittest.TestCase):
    def setUp(self):
        self.harness = ChatHarness(Path('/tmp'))
        self.harness.client.api_key = 'sk-test-key'
        for name in ('one', 'two'):
            session = ChatSession(thread_id=f'thread-{name}')
            self.harness.sessions[name] = session
            self.harness.by_thread[session.thread_id] = session
        self.harness.background['background'] = (SimpleNamespace(done=lambda: True), None)

    def events(self, name='one'):
        return [event[1:] for event in self.harness.sessions[name].events]

    def notify(self, method, item, thread='thread-one', **extra):
        self.harness.notification(method, {'threadId': thread, 'turnId': 'turn-1', 'item': item, **extra})

    def test_dynamic_lifecycle_and_isolation(self):
        item = {'type': 'dynamicToolCall', 'id': 'call-42', 'namespace': None, 'tool': 'vector_search',
                'arguments': {'query': 'Azure growth', 'limit': 5}, 'status': 'inProgress',
                'contentItems': None, 'success': None, 'durationMs': None}
        self.notify('item/started', item, startedAtMs=1000)
        content = [{'type': 'inputText', 'text': '[{"episode": "Example"}]'}]
        self.notify('item/completed', {**item, 'status': 'completed', 'contentItems': content,
                                       'success': True, 'durationMs': 340}, completedAtMs=1340)
        started, completed = self.events()
        self.assertEqual(started[0], 'tool')
        self.assertEqual(started[1], {
            'item_id': 'call-42', 'turn_id': 'turn-1', 'kind': 'dynamicToolCall', 'phase': 'started',
            'name': 'vector_search', 'arguments': {'query': 'Azure growth', 'limit': 5}, 'result': None,
            'status': 'running', 'started_at_ms': 1000, 'completed_at_ms': None, 'duration_ms': None,
            'approximate_times': False, 'truncated': False})
        self.assertEqual(completed[1]['phase'], 'completed')
        self.assertEqual(completed[1]['status'], 'succeeded')
        self.assertEqual(completed[1]['result'], {'contentItems': content, 'success': True})
        self.assertEqual((completed[1]['started_at_ms'], completed[1]['completed_at_ms'],
                          completed[1]['duration_ms']), (1000, 1340, 340))
        # Thread isolation: another chat and a background ingestion thread see nothing.
        self.notify('item/started', item, thread='background', startedAtMs=1000)
        self.assertEqual(self.events('two'), [])
        self.assertEqual(len(self.events()), 2)

    def test_reasoning_and_other_items_are_not_forwarded(self):
        reasoning = {'type': 'reasoning', 'id': 'r-1', 'summary': ['secret plan'], 'content': []}
        self.notify('item/started', reasoning)
        self.notify('item/completed', reasoning)
        self.harness.notification('item/reasoning/textDelta', {'threadId': 'thread-one', 'itemId': 'r-1', 'delta': 'x'})
        self.harness.notification('item/reasoning/summaryTextDelta', {'threadId': 'thread-one', 'itemId': 'r-1', 'delta': 'x'})
        self.notify('item/started', {'type': 'plan', 'id': 'p-1', 'text': 'plan'})
        self.notify('item/started', {'type': 'agentMessage', 'id': 'm-1', 'text': ''})
        self.assertEqual(self.events(), [])

    def test_agent_messages_still_stream_once(self):
        self.harness.notification('item/agentMessage/delta', {'threadId': 'thread-one', 'itemId': 'm-1', 'delta': 'Hi'})
        self.notify('item/completed', {'type': 'agentMessage', 'id': 'm-1', 'text': 'Hi there'})
        self.assertEqual(self.events(), [('delta', {'item_id': 'm-1', 'text': 'Hi'}),
                                         ('message', {'item_id': 'm-1', 'text': 'Hi there'})])

    def test_web_search_without_results_is_not_fabricated(self):
        item = {'type': 'webSearch', 'id': 'ws-1', 'query': 'Azure capex',
                'action': {'type': 'search', 'query': 'Azure capex', 'queries': None}, 'results': None}
        self.notify('item/completed', item, completedAtMs=2000)
        [(_, tool)] = self.events()
        self.assertEqual(tool['name'], 'web_search')
        self.assertEqual(tool['arguments']['query'], 'Azure capex')
        self.assertIsNone(tool['result'])
        self.assertEqual(tool['status'], 'succeeded')
        # A completion with no earlier start is self-sufficient.
        self.assertEqual((tool['phase'], tool['completed_at_ms'], tool['started_at_ms']), ('completed', 2000, None))

    def test_command_and_mcp_projection(self):
        self.notify('item/completed', {
            'type': 'commandExecution', 'id': 'cmd-1', 'command': 'ls  -la', 'cwd': '/w', 'status': 'declined',
            'aggregatedOutput': 'x' * 50_000, 'exitCode': None, 'durationMs': 5}, completedAtMs=10)
        self.notify('item/completed', {
            'type': 'mcpToolCall', 'id': 'mcp-1', 'server': 'docs', 'tool': 'search', 'status': 'failed',
            'arguments': {'q': 'x'}, 'result': None, 'error': {'message': 'boom'}, 'durationMs': None})
        (_, command), (_, mcp) = self.events()
        self.assertEqual((command['name'], command['status'], command['arguments']),
                         ('command_execution', 'declined', {'command': 'ls  -la', 'cwd': '/w'}))
        self.assertTrue(command['truncated'])
        self.assertEqual(len(command['result']['aggregatedOutput']), 40_000)
        self.assertEqual((command['started_at_ms'], command['duration_ms']), (5, 5))
        self.assertEqual((mcp['name'], mcp['status']), ('docs.search', 'failed'))
        self.assertEqual(mcp['result'], {'result': None, 'error': {'message': 'boom'}})
        self.assertTrue(mcp['approximate_times'])

    def test_failed_dynamic_tool_and_redaction(self):
        self.notify('item/completed', {
            'type': 'dynamicToolCall', 'id': 'call-1', 'namespace': 'podcast', 'tool': 'postgres_database_query',
            'arguments': {'sql': 'SELECT 1', 'headers': {'Authorization': 'Bearer abc'}, 'note': 'key sk-test-key'},
            'status': 'completed', 'contentItems': [{'type': 'inputText', 'text': 'Error: bad sk-test-key'}],
            'success': False, 'durationMs': 3}, completedAtMs=10)
        [(_, tool)] = self.events()
        self.assertEqual((tool['name'], tool['status']), ('podcast.postgres_database_query', 'failed'))
        self.assertEqual(tool['arguments'], {'sql': 'SELECT 1', 'headers': {'Authorization': '[redacted]'},
                                             'note': 'key [redacted]'})
        self.assertEqual(tool['result']['contentItems'][0]['text'], 'Error: bad [redacted]')
        self.assertFalse(tool['truncated'])


if __name__ == '__main__':
    unittest.main()
