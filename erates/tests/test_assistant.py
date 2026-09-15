import json
from datetime import timedelta
from unittest import mock

from django.contrib.gis.geos import Polygon
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from erates import assistant
from erates.models import Parcel, Payment, User
from erates.payment_flow import generate_rate_bills


def _model_reply(messages):
    yield 'Model '
    yield 'commentary.'


def _model_down(messages):
    raise assistant.AssistantError('The assistant model is unreachable.')
    yield


@override_settings(LLM_API_URL='http://model.test/v1')
class AssistantTests(APITestCase):
    def setUp(self):
        self.official = User.objects.create_user('admin', 'admin@example.com', 'Password123!', role='admin', county='Nyeri')
        owner = User.objects.create_user('wanjiru', 'w@example.com', 'Password123!', phone='0712345678')
        for i, (ref, county) in enumerate([('1865', 'Nyeri'), ('317', 'Nyeri'), ('900', 'Kiambu')]):
            Parcel.objects.create(
                parcel_ref=ref, geom=Polygon.from_bbox((36.8 + i / 100, -1.3, 36.801 + i / 100, -1.299)),
                county=county, sub_county='Tetu', ward='karura', owner_user=owner, props={'REG_SECTIO': 'AGUTHI-GAAKI'},
            )
        generate_rate_bills(2026, timezone.now() + timedelta(days=30))
        Payment.objects.filter(parcel__parcel_ref__in=['317', '900']).update(deadline=timezone.now() - timedelta(days=10))
        self.client.force_authenticate(self.official)

    def ask(self, question, history=None, model=_model_reply):
        body = {'query': question, **({'history': history} if history else {})}
        with mock.patch.object(assistant, '_stream_llm', side_effect=model) as llm:
            resp = self.client.post('/api/llm/analyze/', body, format='json')
            events = [json.loads(line) for line in b''.join(resp.streaming_content).decode().splitlines()]
        return events, llm

    @staticmethod
    def text(events):
        return ''.join(e.get('text', '') for e in events)

    def test_who_are_you_goes_to_the_model_without_data(self):
        events, llm = self.ask('who are you')
        self.assertNotIn('sources', events[0])
        self.assertEqual(self.text(events), 'Model commentary.')
        self.assertEqual(llm.call_args.args[0][-1]['content'], 'QUESTION: who are you')

    def test_table_arrives_before_the_commentary(self):
        events, _ = self.ask('Which defaulters are in karura ward?')
        self.assertEqual(events[0]['sources'], [{'tool': 'defaulters', 'args': {'ward': 'karura'}}])
        self.assertIn('| 317 |', events[0]['text'])
        self.assertTrue(self.text(events).endswith('Model commentary.'))

    def test_sentences_with_invented_figures_are_dropped(self):
        def invents(messages):
            yield 'There is 1 overdue bill. It totals 99,999 '
            yield 'in arrears. Arrears stand at KES 4,800.00. Another 42 plots are late.'
        events, _ = self.ask('show defaulters', model=invents)
        self.assertTrue(self.text(events).endswith('\n\nThere is 1 overdue bill. Arrears stand at KES 4,800.00. '))

    def test_code_comments_when_every_model_sentence_is_dropped(self):
        def invents_everything(messages):
            yield 'Collections reached KES 270,395 across 42 plots.'
        events, _ = self.ask('show defaulters', model=invents_everything)
        self.assertTrue(self.text(events).endswith('\n\n1 overdue bill; the oldest has been unpaid for 10 days.'))

    def test_a_bare_follow_up_reuses_the_previous_question(self):
        history = [{'role': 'user', 'text': 'do we have any data on Nyeri county'}, {'role': 'assistant', 'text': 'Which year?'}]
        events, _ = self.ask('2026', history=history)
        self.assertEqual(events[0]['sources'], [{'tool': 'collections_summary', 'args': {'year': 2026}}])

    def test_never_silent_when_every_sentence_is_dropped(self):
        def invents(messages):
            yield 'There are 500 plots in 2026.'
        events, _ = self.ask('who are you', model=invents)
        self.assertEqual(self.text(events), assistant.HELP_TEXT)

    def test_superadmin_can_list_counties(self):
        self.client.force_authenticate(User.objects.create_superuser('root', 'root@example.com', 'Password123!'))
        events, _ = self.ask('which counties do we have?')
        self.assertIn('| Kiambu |', events[0]['text'])
        self.assertIn('| Nyeri |', events[0]['text'])

    def test_model_sees_counts_not_people_or_phones(self):
        events, llm = self.ask('List karura ward plots')
        self.assertEqual(events[0]['sources'], [{'tool': 'ward_parcels', 'args': {'ward': 'karura'}}])
        prompt = json.dumps(llm.call_args.args[0])
        self.assertIn('FACTS', prompt)
        self.assertNotIn('0712345678', prompt)
        self.assertNotIn('wanjiru', prompt)

    def test_multi_year_question_reaches_years_summary(self):
        events, llm = self.ask('Which years are unpaid for?')
        self.assertEqual(events[0]['sources'], [{'tool': 'years_summary', 'args': {}}])
        self.assertIn('| 2026 |', events[0]['text'])
        self.assertIn('years_with_unpaid_bills', llm.call_args.args[0][-1]['content'])

    def test_officials_cannot_ask_about_another_county(self):
        events, llm = self.ask('show kiambu defaulters')
        self.assertIn('Cross-County Restriction', self.text(events))
        llm.assert_not_called()

    def test_officials_only_see_their_own_county(self):
        events, _ = self.ask('show defaulters')
        self.assertIn('| 317 |', events[0]['text'])
        self.assertNotIn('| 900 |', events[0]['text'])
        events, _ = self.ask('status of plot 900')
        self.assertIn('No plot', events[0]['text'])

    def test_superadmin_sees_every_county(self):
        self.client.force_authenticate(User.objects.create_superuser('root', 'root@example.com', 'Password123!'))
        events, _ = self.ask('show defaulters')
        self.assertIn('| 317 |', events[0]['text'])
        self.assertIn('| 900 |', events[0]['text'])

    def test_official_without_county_is_restricted(self):
        self.official.county = ''
        self.official.save()
        self.client.force_authenticate(self.official)
        events, llm = self.ask('show defaulters')
        self.assertIn('Access Restricted', self.text(events))
        llm.assert_not_called()

    def test_model_failure_after_the_table_is_reported(self):
        events, _ = self.ask('how much have we collected?', model=_model_down)
        self.assertEqual(events[0]['sources'], [{'tool': 'collections_summary', 'args': {}}])
        self.assertEqual(events[-1], {'error': 'The assistant model is unreachable.'})

    def test_pdf_requests_skip_the_model(self):
        events, llm = self.ask('export arrears report to pdf')
        self.assertEqual(events[0]['sources'], [{'tool': 'generate_report_pdf', 'args': {'report_type': 'arrears'}}])
        self.assertIn('/api/reports/ai-download/', events[0]['text'])
        llm.assert_not_called()

    @override_settings(LLM_API_URL='')
    def test_missing_model_url_still_serves_tables(self):
        events, _ = self.ask('show defaulters')
        self.assertIn('| 317 |', self.text(events))
        events, _ = self.ask('who are you')
        self.assertEqual(events, [{'error': 'The assistant model is not configured on the server.'}])

    def test_routes(self):
        self.assertEqual(assistant.route('who are you'), [])
        self.assertEqual(assistant.route('status of plot 1865'), [('plot_lookup', {'plot': '1865'})])
        self.assertEqual(assistant.route('how much was collected in 2025'), [('collections_summary', {'year': 2025})])
        self.assertEqual(assistant.route('show plots in mugunda ward'), [('ward_parcels', {'ward': 'mugunda'})])
        self.assertEqual(assistant.route('Which plots are owned by wanjiru?'), [('owner_lookup', {'owner': 'wanjiru'})])
        self.assertEqual(assistant.route('generate an analysis pdf report for 2026'), [('generate_analysis_pdf', {'year': 2026})])

    def test_history_reaches_the_model_trimmed(self):
        history = [
            {'role': 'user', 'text': 'Who owns plot 1865?'},
            {'role': 'assistant', 'text': 'x' * 1000 + ' Plot 1865 belongs to wanjiru.'},
        ]
        _, llm = self.ask('who are you', history=history)
        messages = llm.call_args.args[0]
        self.assertEqual(messages[1], {'role': 'user', 'content': 'Who owns plot 1865?'})
        self.assertEqual(len(messages[2]['content']), assistant.MAX_HISTORY_CHARS)
        self.assertTrue(messages[2]['content'].endswith('belongs to wanjiru.'))

    def test_history_longer_than_the_cap_is_rejected(self):
        history = [{'role': 'user', 'text': f'q{i}'} for i in range(7)]
        resp = self.client.post('/api/llm/analyze/', {'query': 'x', 'history': history}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_ratepayers_cannot_use_assistant(self):
        self.client.force_authenticate(User.objects.get(username='wanjiru'))
        self.assertEqual(self.client.post('/api/llm/analyze/', {'query': 'hi'}, format='json').status_code, 403)

    def test_years_summary_separates_paid_from_outstanding(self):
        from erates import rate_reports
        rows = {r['year']: r for r in rate_reports.years()}
        self.assertIn(2026, rows)
        self.assertEqual(rows[2026]['bills'], rows[2026]['paid_bills'] + rows[2026]['unpaid_bills'])
        self.assertEqual(rows[2026]['billed'], rows[2026]['collected'] + rows[2026]['outstanding'])

    def test_owner_can_be_found_by_display_name_not_just_username(self):
        User.objects.filter(username='wanjiru').update(username='jane_wanjiru')
        self.assertIsNotNone(assistant._find_owner('Jane Wanjiru'))
        self.assertIsNotNone(assistant._find_owner('jane_wanjiru'))
        self.assertIsNotNone(assistant._find_owner('0712345678'))
        self.assertIsNotNone(assistant._find_owner('JaneWanjiru'))
        self.assertIsNone(assistant._find_owner('nobody at all'))

    def test_ai_download_view_serves_pdf(self):
        from erates import ai_reports
        result = ai_reports.build_executive_analysis_pdf(year=2026)
        dl_resp = self.client.get(f'/api/reports/ai-download/?file={result["filename"]}')
        self.assertEqual(dl_resp.status_code, 200)
        self.assertEqual(dl_resp['Content-Type'], 'application/pdf')
        self.assertTrue(dl_resp.content.startswith(b'%PDF'))

    def test_ai_download_view_rejects_invalid_file(self):
        self.assertEqual(self.client.get('/api/reports/ai-download/?file=../../etc/passwd').status_code, 400)
        self.assertEqual(self.client.get('/api/reports/ai-download/?file=non_existent_file.pdf').status_code, 404)
