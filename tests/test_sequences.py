import unittest
from model.sequences import enumerate_plans,shortest_certificate,modeled_failure,validate,Node,step


class SequenceTests(unittest.TestCase):
    def test_certificate_exhausts_all_shorter_terminal_plans(self):
        c=shortest_certificate()
        self.assertEqual(c['length'],3)
        shorter=[p for p in enumerate_plans(c['length']-1)]
        self.assertEqual(c['strictly_shorter_terminal_plans'],shorter)
        self.assertTrue(all(not modeled_failure(p) for p in shorter))
        self.assertTrue(modeled_failure(c['shortest']))
    def test_cache_and_policy_context_change_failure(self):
        self.assertTrue(modeled_failure(['disconnect','arm_reject','reconnect']))
        self.assertFalse(modeled_failure(['disconnect','flush_sta','arm_reject','reconnect']))
        self.assertFalse(modeled_failure(['disconnect','arm_reject','allow','reconnect']))
    def test_multicycle_legal_and_illegal_sequences(self):
        self.assertEqual(len(validate(['disconnect','reconnect','disconnect','flush_ap','reconnect'])),5)
        for plan in [[],['reconnect'],['flush_ap','reconnect'],['disconnect','disconnect','reconnect']]:
            with self.assertRaises(ValueError):validate(plan)
    def test_enumerator_is_breadth_first_and_all_plans_legal(self):
        plans=list(enumerate_plans(5))
        self.assertEqual([len(p) for p in plans],sorted(len(p) for p in plans))
        for p in plans:validate(p)

    def test_shallow_guided_pool_stops_and_records_actual_budget(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from sequence_study import study
        def replay(plan,out):
            return {'recovery':'pass','failure':None,'cleanup':{'errors':[]},'elapsed_s':1,
                    'operations':[{'finished_s':1} for _ in plan]},out/'example'
        with tempfile.TemporaryDirectory() as tmp,patch('sequence_study.replay',replay):
            out=study('guided',tmp,budget=6,depth=2)
            result=json.loads((Path(out)/'summary.json').read_text())
            self.assertEqual(result['stop_reason'],'legal_pool_exhausted')
            self.assertEqual(result['metrics']['proposals'],1)
            self.assertEqual(result['budget'],6)
            with self.assertRaises(ValueError):study('shortest',tmp,depth=2)

    def test_multicycle_failure_uses_later_rejection_evidence(self):
        from sequence_study import metrics
        result={'recovery':'fail','failure':{'category':'recovery_timeout'},'cleanup':{'errors':[]},
                'elapsed_s':1,'operations':[],'independent_observation':{
                    'first_rejection':{'status_code':53},'effective_fault':True,'detected_recovery_failure':True,
                    'timeline':[{'reason_code':None},{'reason_code':9}]}}
        measured=metrics([{'valid':True,'result':result}])
        self.assertEqual(measured['confirmed_rejection_failure_sequences'],1)
        result['independent_observation']['timeline']=[]
        self.assertEqual(metrics([{'valid':True,'result':result}])['confirmed_rejection_failure_sequences'],0)
