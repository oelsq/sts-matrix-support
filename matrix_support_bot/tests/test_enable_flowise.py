"""Promotion preserves room identities, existing routes and operator rollback choices."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

p=Path(__file__).resolve().parents[2]/'scripts/enable_flowise.py'
spec=importlib.util.spec_from_file_location('enable_flowise',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class PrimaryFlowiseTests(unittest.TestCase):
    def setUp(self):
        self.main={'!legal:demo.local':'legal','!finance:demo.local':'finance'}
        self.setup={'flows':{'legal':'11111111-1111-4111-8111-111111111111','finance':'22222222-2222-4222-8222-222222222222'},
                    'review_flows':{'legal':'33333333-3333-4333-8333-333333333333','finance':'44444444-4444-4444-8444-444444444444'}}
        self.old={'!lab:demo.local':{'domain':'legal','flow_id':self.setup['flows']['legal']}}
    def test_promotes_existing_rooms_and_keeps_lab(self):
        result=mod.primary_routes(self.main,self.setup,self.old)
        self.assertEqual(set(result),set(self.main)|set(self.old))
        self.assertEqual(result['!lab:demo.local'],self.old['!lab:demo.local'])
        for r,d in self.main.items():
            self.assertEqual(result[r]['domain'],d)
            self.assertEqual(result[r]['flow_id'],self.setup['flows'][d])
            self.assertEqual(result[r]['review_flow_id'],self.setup['review_flows'][d])
        self.assertEqual(mod.primary_routes(self.main,self.setup,result),result)
    def test_domain_conflict_and_bad_flow_ids_stop_promotion(self):
        with self.assertRaises(ValueError):mod.primary_routes(self.main,self.setup,{'!legal:demo.local':{'domain':'finance','flow_id':self.setup['flows']['finance']}})
        bad=json.loads(json.dumps(self.setup));bad['review_flows']['legal']=bad['flows']['legal']
        with self.assertRaises(ValueError):mod.primary_routes(self.main,bad,self.old)
        with self.assertRaises(ValueError):mod.primary_routes({'!legal:demo.local':'legal'},self.setup,self.old)
    def test_backup_apply_rollback_and_independent_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);routes=root/'routes.json';main=root/'main.json';setup=root/'setup.json';backup=root/'backup.json'
            for p,value in [(routes,self.old),(main,self.main),(setup,self.setup)]:p.write_text(json.dumps(value))
            self.assertTrue(mod.enable(routes,main,setup,backup))
            self.assertEqual(backup.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(main.read_text()),self.main)
            self.assertFalse(mod.enable(routes,main,setup,backup))
            self.assertTrue(mod.rollback(routes,backup));self.assertEqual(json.loads(routes.read_text()),self.old)
            self.assertFalse(mod.rollback(routes,backup))
            routes.write_text('{}')
            with self.assertRaises(ValueError):mod.rollback(routes,backup)
    def test_existing_backup_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);routes=root/'routes.json';main=root/'main.json';setup=root/'setup.json';backup=root/'backup.json'
            for p,value in [(routes,self.old),(main,self.main),(setup,self.setup),(backup,{'old':True})]:p.write_text(json.dumps(value))
            with self.assertRaises(ValueError):mod.enable(routes,main,setup,backup)
            self.assertEqual(json.loads(routes.read_text()),self.old)
            self.assertEqual(json.loads(backup.read_text()),{'old':True})
if __name__=='__main__':unittest.main()
