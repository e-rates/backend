import json
from unittest import mock

from django.test import override_settings
from rest_framework.test import APITestCase

from erates import assistant
from erates.models import Conversation, User


def reply(messages):
    yield 'Hello there.'


@override_settings(LLM_API_URL='http://model.test/v1')
class ConversationTests(APITestCase):
    def setUp(self):
        self.official = User.objects.create_user('admin', 'a@example.com', 'Password123!', role='admin', county='Nyeri')
        self.client.force_authenticate(self.official)

    def ask(self, query, conversation=None):
        body = {'query': query, **({'conversation': conversation} if conversation else {})}
        with mock.patch.object(assistant, '_stream_llm', side_effect=reply) as llm:
            resp = self.client.post('/api/llm/analyze/', body, format='json')
            events = []
            if resp.status_code == 200:
                events = [json.loads(line) for line in b''.join(resp.streaming_content).decode().splitlines()]
        return resp, events, llm

    def test_a_new_question_starts_a_saved_conversation(self):
        _, events, _ = self.ask('who are you')
        conversation = Conversation.objects.get(pk=events[0]['conversation'])
        self.assertEqual(conversation.title, 'who are you')
        self.assertEqual(
            [(m['role'], m['text']) for m in conversation.messages],
            [('user', 'who are you'), ('assistant', 'Hello there.')],
        )

    def test_continuing_sends_earlier_turns_to_the_model(self):
        _, events, _ = self.ask('who are you')
        conversation_id = events[0]['conversation']
        _, events, llm = self.ask('and what can you do', conversation_id)
        self.assertEqual(events[0]['conversation'], conversation_id)
        self.assertEqual([m['content'] for m in llm.call_args.args[0][1:3]], ['who are you', 'Hello there.'])
        self.assertEqual(len(Conversation.objects.get(pk=conversation_id).messages), 4)

    def test_officials_only_see_their_own_conversations(self):
        _, events, _ = self.ask('who are you')
        conversation_id = events[0]['conversation']
        other = User.objects.create_user('other', 'o@example.com', 'Password123!', role='admin', county='Nyeri')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get('/api/conversations/').json(), [])
        self.assertEqual(self.client.get(f'/api/conversations/{conversation_id}/').status_code, 404)
        resp, _, _ = self.ask('hi', conversation_id)
        self.assertEqual(resp.status_code, 404)

    def test_list_open_and_delete(self):
        _, events, _ = self.ask('who are you')
        conversation_id = events[0]['conversation']
        self.assertEqual([c['title'] for c in self.client.get('/api/conversations/').json()], ['who are you'])
        self.assertEqual(len(self.client.get(f'/api/conversations/{conversation_id}/').json()['messages']), 2)
        self.assertEqual(self.client.delete(f'/api/conversations/{conversation_id}/').status_code, 204)
        self.assertEqual(self.client.get('/api/conversations/').json(), [])
