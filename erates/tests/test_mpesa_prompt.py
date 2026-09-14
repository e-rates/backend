from django.test import SimpleTestCase, override_settings

from erates import mpesa


class AccountReferenceTests(SimpleTestCase):
    @override_settings(MPESA_ACCOUNT_PREFIX='')
    def test_plot_number_is_used_as_is(self):
        self.assertEqual(mpesa.account_reference('1120'), '1120')

    @override_settings(MPESA_ACCOUNT_PREFIX='')
    def test_separators_are_stripped_and_case_is_preserved(self):
        self.assertEqual(mpesa.account_reference('aguthi-gaaki/1120'), 'aguthigaaki1')

    @override_settings(MPESA_ACCOUNT_PREFIX='LR')
    def test_prefix_is_applied(self):
        self.assertEqual(mpesa.account_reference('1120'), 'LR1120')

    @override_settings(MPESA_ACCOUNT_PREFIX='COUNTYRATES')
    def test_a_long_prefix_never_crowds_out_the_plot_number(self):
        # The plot number is what identifies the bill, so it wins over the prefix.
        self.assertEqual(mpesa.account_reference('1120'), '1120')

    @override_settings(MPESA_ACCOUNT_PREFIX='')
    def test_reference_never_exceeds_the_safaricom_cap(self):
        self.assertEqual(len(mpesa.account_reference('A' * 40)), mpesa.ACCOUNT_REF_MAX)

    @override_settings(MPESA_ACCOUNT_PREFIX='')
    def test_empty_reference_is_refused(self):
        with self.assertRaises(mpesa.MpesaError):
            mpesa.account_reference('///')


class DescriptionTests(SimpleTestCase):
    def test_year_is_never_truncated_mid_digit(self):
        desc = mpesa.transaction_description(2026)
        self.assertEqual(desc, 'LandRates2026')
        self.assertLessEqual(len(desc), mpesa.DESCRIPTION_MAX)
        self.assertTrue(desc.endswith('2026'))

    def test_missing_year_still_says_what_the_payment_is_for(self):
        self.assertEqual(mpesa.transaction_description(None), 'Land rates')
