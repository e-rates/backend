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


@override_settings(LLM_API_URL='https://colab.example/x')
class AssistantTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', 'admin@example.com', 'Password123!', role='admin')
        owner = User.objects.create_user('wanjiru', 'w@example.com', 'Password123!', phone='0712345678')
        for i, ref in enumerate(['1865', '317']):
            Parcel.objects.create(
                parcel_ref=ref, geom=Polygon.from_bbox((36.8 + i / 100, -1.3, 36.801 + i / 100, -1.299)),
                county='Nyeri', sub_county='Tetu', ward='karura', owner_user=owner, props={'REG_SECTIO': 'AGUTHI-GAAKI'},
            )
        generate_rate_bills(2026, timezone.now() + timedelta(days=30))
        Payment.objects.filter(parcel__parcel_ref='317').update(deadline=timezone.now() - timedelta(days=10))
        self.client.force_authenticate(self.admin)

    def ask(self, question, replies):
        with mock.patch.object(assistant, '_call_llm', side_effect=replies) as llm:
            resp = self.client.post('/api/llm/analyze/', {'query': question}, format='json')
        return resp, llm

    def test_model_chosen_tool_feeds_the_answer(self):
        plan = 'Sure! {"tools": [{"name": "plot_lookup", "args": {"plot": "AGUTHI-GAAKI/1865"}}]}'
        resp, llm = self.ask('Who owns plot 1865?', [plan, 'Plot 1865 belongs to wanjiru.'])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['answer'], 'Plot 1865 belongs to wanjiru.')
        self.assertEqual(body['sources'], [{'tool': 'plot_lookup', 'args': {'plot': 'AGUTHI-GAAKI/1865'}}])
        self.assertFalse(body['used_fallback'])
        compose_prompt = llm.call_args_list[1].args[0]
        self.assertIn('"owner":"wanjiru"', compose_prompt)
        self.assertIn('AGUTHI-GAAKI/1865', compose_prompt)

    def test_unparseable_plan_uses_keyword_fallback(self):
        resp, llm = self.ask('Which defaulters are in karura ward?', ['I would look at defaulters.', 'One defaulter.'])
        body = resp.json()
        self.assertTrue(body['used_fallback'])
        self.assertEqual(body['sources'], [{'tool': 'defaulters', 'args': {'ward': 'karura'}}])
        data = json.loads(llm.call_args_list[1].args[0].split('DATA: ')[1].split('\n\nQUESTION')[0])
        self.assertEqual([d['plot'] for d in data[0]['data']['defaulters']], ['317'])

    def test_multi_year_question_reaches_a_tool_that_spans_years(self):
        resp, llm = self.ask('Which years are unpaid for?', ['no json here', 'Both 2025 and 2026 have unpaid bills.'])
        body = resp.json()
        self.assertTrue(body['used_fallback'])
        self.assertEqual(body['sources'], [{'tool': 'years_summary', 'args': {}}])
        data = json.loads(llm.call_args_list[1].args[0].split('DATA: ')[1].split('\n\nQUESTION')[0])
        self.assertIn(2026, data[0]['data']['years_with_unpaid_bills'])

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
        self.assertIsNotNone(assistant._find_owner('JaneWanjiru'))  # model drops the space
        self.assertIsNone(assistant._find_owner('nobody at all'))

    def test_owner_questions_route_to_owner_lookup(self):
        resp, _ = self.ask('Which plots are owned by wanjiru?', ['not json', 'Two plots.'])
        self.assertEqual(resp.json()['sources'], [{'tool': 'owner_lookup', 'args': {'owner': 'wanjiru'}}])

    def test_compose_prompt_forbids_one_word_answers(self):
        _, llm = self.ask('Which years are unpaid for?', ['nope', 'Both years.'])
        self.assertIn('never a single word', llm.call_args_list[1].args[0])

    def test_phones_are_masked_before_leaving_the_server(self):
        plan = '{"tools": [{"name": "ward_parcels", "args": {"ward": "karura"}}, {"name": "owner_lookup", "args": {"owner": "wanjiru"}}]}'
        _, llm = self.ask('List karura plots', [plan, 'ok'])
        prompt = llm.call_args_list[1].args[0]
        self.assertNotIn('0712345678', prompt)
        self.assertIn('678', prompt)

    def test_unknown_tools_are_ignored(self):
        plan = '{"tools": [{"name": "drop_table", "args": {}}, {"name": "ward_summary", "args": {"year": 2026}}]}'
        resp, _ = self.ask('Wards?', [plan, 'ok'])
        self.assertEqual(resp.json()['sources'], [{'tool': 'ward_summary', 'args': {'year': 2026}}])

    def test_fallback_routes(self):
        self.assertEqual(assistant.fallback_plan('status of plot 1865'), [('plot_lookup', {'plot': '1865'})])
        self.assertEqual(assistant.fallback_plan('how much was collected in 2025'), [('collections_summary', {'year': 2025})])
        self.assertEqual(assistant.fallback_plan('show plots in mugunda ward'), [('ward_parcels', {'ward': 'mugunda'})])

    @override_settings(LLM_API_URL='')
    def test_missing_model_url_is_503(self):
        resp = self.client.post('/api/llm/analyze/', {'query': 'hi'}, format='json')
        self.assertEqual(resp.status_code, 503)

    def test_ratepayers_cannot_use_assistant(self):
        self.client.force_authenticate(User.objects.get(username='wanjiru'))
        self.assertEqual(self.client.post('/api/llm/analyze/', {'query': 'hi'}, format='json').status_code, 403)

    def test_history_reaches_compose_but_not_the_plan(self):
        plan = '{"tools": [{"name": "plot_lookup", "args": {"plot": "AGUTHI-GAAKI/1865"}}]}'
        history = [
            {'role': 'user', 'text': 'Who owns plot 1865?'},
            {'role': 'assistant', 'text': 'Plot 1865 belongs to wanjiru.'},
        ]
        with mock.patch.object(assistant, '_call_llm', side_effect=[plan, 'It is unpaid.']) as llm:
            resp = self.client.post(
                '/api/llm/analyze/',
                {'query': 'Has it been paid?', 'history': history},
                format='json',
            )
        self.assertEqual(resp.status_code, 200)
        plan_prompt, compose_prompt = (c.args[0] for c in llm.call_args_list)
        self.assertNotIn('wanjiru', plan_prompt)
        self.assertIn('EARLIER IN THIS CONVERSATION', compose_prompt)
        self.assertIn('Plot 1865 belongs to wanjiru.', compose_prompt)

    def test_history_keeps_the_most_recent_turns(self):
        plan = '{"tools": [{"name": "collections_summary", "args": {"year": 2026}}]}'
        history = [{'role': 'user', 'text': f'question {i}'} for i in range(6)]
        with mock.patch.object(assistant, '_call_llm', side_effect=[plan, 'ok']) as llm:
            resp = self.client.post(
                '/api/llm/analyze/',
                {'query': 'And now?', 'history': history},
                format='json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('question 5', llm.call_args_list[1].args[0])

    def test_history_longer_than_the_cap_is_rejected(self):
        history = [{'role': 'user', 'text': f'q{i}'} for i in range(7)]
        resp = self.client.post(
            '/api/llm/analyze/', {'query': 'x', 'history': history}, format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_question_without_history_still_works(self):
        plan = '{"tools": [{"name": "collections_summary", "args": {"year": 2026}}]}'
        resp, _ = self.ask('How much have we collected?', [plan, 'One shilling.'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['answer'], 'One shilling.')

    def test_generate_analysis_pdf_tool(self):
        plan = '{"tools": [{"name": "generate_analysis_pdf", "args": {"year": 2026}}]}'
        resp, _ = self.ask('Generate an executive analysis PDF for 2026', [plan, 'Here is the analysis report.'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['sources'], [{'tool': 'generate_analysis_pdf', 'args': {'year': 2026}}])
        self.assertEqual(resp.json()['answer'], 'Here is the analysis report.')

    def test_generate_report_pdf_fallback_routing(self):
        resp, llm = self.ask('export arrears report to pdf', ['no json', 'Here is the arrears PDF.'])
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['used_fallback'])
        self.assertEqual(resp.json()['sources'], [{'tool': 'generate_report_pdf', 'args': {'report_type': 'arrears'}}])

    def test_generate_analysis_pdf_fallback_routing(self):
        resp, llm = self.ask('generate an analysis pdf report for 2026', ['no json', 'Here is the executive briefing.'])
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['used_fallback'])
        self.assertEqual(resp.json()['sources'], [{'tool': 'generate_analysis_pdf', 'args': {'year': 2026}}])

    def test_ai_download_view_serves_pdf(self):
        from erates import ai_reports
        result = ai_reports.build_executive_analysis_pdf(year=2026)
        filename = result['filename']
        dl_resp = self.client.get(f'/api/reports/ai-download/?file={filename}')
        self.assertEqual(dl_resp.status_code, 200)
        self.assertEqual(dl_resp['Content-Type'], 'application/pdf')
        self.assertTrue(dl_resp.content.startswith(b'%PDF'))

    def test_ai_download_view_rejects_invalid_file(self):
        dl_resp = self.client.get('/api/reports/ai-download/?file=../../etc/passwd')
        self.assertEqual(dl_resp.status_code, 400)
        dl_resp404 = self.client.get('/api/reports/ai-download/?file=non_existent_file.pdf')
        self.assertEqual(dl_resp404.status_code, 404)
