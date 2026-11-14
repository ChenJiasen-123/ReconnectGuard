import unittest
from unittest.mock import patch
from runner import ROOT, load_case, run_case
from comparison import run_comparison


class RadioSelectionTest(unittest.TestCase):
    def test_physical_mock_is_rejected_before_session(self):
        case=load_case(ROOT/'cases.yaml','ap_deauth')
        with patch('runner.MockSession') as session:
            with self.assertRaises(ValueError):
                run_case(case, radio='physical')
            session.assert_not_called()

    def test_comparison_rejects_unknown_radio(self):
        with self.assertRaises(ValueError):
            run_comparison(ROOT/'cases.yaml', ROOT/'results', radio='invalid')
