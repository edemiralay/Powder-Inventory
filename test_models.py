import copy
import base64
import io
import itertools
import json
import unittest
import numpy as np
import powder_inventory_app as app


def inputs():
    p = copy.deepcopy(app.DEFAULT)
    p['settings'].update(search_candidates=10, simulation_years=1, replications=2,
                         training_replications=1, warmup_years=0, max_review_days=60)
    p['demand'].update(mu=30, sigma=0)
    return p


class DemandTests(unittest.TestCase):
    def test_classification_thresholds_cannot_be_overridden(self):
        for occurrence in (None, .3):
            p=inputs();p['demand'].update(mu=100,sigma=15,occurrence_probability=occurrence)
            expected=app.characterize_inputs(p['demand'],p['settings'])
            p['settings'].update(adi_cutoff=999,cv2_cutoff=0.000001)
            actual=app.characterize_inputs(p['demand'],p['settings'])
            self.assertEqual(actual,expected)

    def test_classification_and_consistent_occurrence(self):
        for sd, occurrence, category, model in [(10,None,'Smooth','Reorder Point'),
                (500,None,'Erratic','Reorder Point'),(10,.3,'Intermittent','Order-Up-To-Level'),
                (500,.3,'Lumpy','Order-Up-To-Level')]:
            p=inputs(); p['demand'].update(mu=100,sigma=sd,occurrence_probability=occurrence)
            c=app.characterize_inputs(p['demand'],p['settings'])
            self.assertEqual(c['category'].lower(),category.lower())
            self.assertEqual(c['model'],model)
            s=app.demand_stats(c['prepared_demand'])
            self.assertAlmostEqual(s['positive_probability'],1 if occurrence is None else occurrence)

    def test_inconsistent_frequent_occurrence_rejected(self):
        for family, occurrence in [('Normal',.9),('Poisson',.95)]:
            p=inputs();p['demand'].update(family=family,lam=1,occurrence_probability=occurrence)
            with self.assertRaises(ValueError):app.characterize_inputs(p['demand'],p['settings'])

    def test_direct_count_distributions(self):
        for family in ['Poisson','Geometric']:
            p=inputs();p['demand'].update(family=family,lam=3,q=.1)
            c=app.characterize_inputs(p['demand'],p['settings'])
            self.assertEqual(c['model'],'Reorder Point')
            self.assertFalse(c['prepared_demand']['positive_component'])

    def test_bernoulli_positive_component_moments(self):
        for family in ['Normal','Poisson','Geometric']:
            p=inputs();p['demand'].update(family=family,mu=10,sigma=3,lam=2,q=.5,unit=2,
                                        occurrence_probability=.3)
            d=app.characterize_inputs(p['demand'],p['settings'])['prepared_demand']
            s=app.demand_stats(d);draw=app.monthly_paths(d,100000,1,42)[0]
            self.assertAlmostEqual(float((draw>0).mean()),.3,delta=.006)
            self.assertAlmostEqual(float(draw.mean()),s['mean'],delta=.03*s['mean'])
            self.assertAlmostEqual(float(draw.var()),s['sd']**2,delta=.05*s['sd']**2)
            if family!='Normal':self.assertGreaterEqual(draw[draw>0].min(),2)

    def test_tiny_positive_poisson(self):
        d=inputs()['demand'];d.update(family='Poisson',lam=1e-7,unit=2,p=1,positive_component=True)
        values=app.monthly_paths(d,1000,1,42)
        self.assertTrue(np.all(np.isfinite(values)))
        self.assertTrue(np.all(values>=2))

    def test_negative_underlying_normal(self):
        d=inputs()['demand'];d.update(mu=-10,sigma=3)
        values=app.monthly_paths(d,1000,1,42)
        self.assertTrue(np.all(values>0))


class SimulationTests(unittest.TestCase):
    def setUp(self):self.p=inputs()

    def check_balances(self,row):
        for key in ['physical_balance_error_kg','inventory_position_error_kg','pipeline_balance_error_kg']:
            self.assertLess(row['diagnostics'][key],1e-7)
        self.assertAlmostEqual(row['annual_cost_eur'],sum(v for k,v in row['costs'].items() if k!='Rev')-row['costs']['Rev'])
        self.assertTrue(0<=row['fill_rate']<=1)

    def test_single_source_policy_equivalence(self):
        paths=np.full((2,24),30.)
        for model in ['rop','outl']:
            rows=[]
            for policy in app.POLICIES:
                if model=='rop':row=app.simulate_rop(self.p,[self.p['sources'][1]],policy,[1],[20],[40],paths)
                else:row=app.simulate_outl(self.p,[self.p['sources'][1]],policy,[1],[40],[15],paths)
                self.check_balances(row);rows.append(row)
            for row in rows[1:]:
                self.assertAlmostEqual(row['annual_cost_eur'],rows[0]['annual_cost_eur'])
                self.assertAlmostEqual(row['fill_rate'],rows[0]['fill_rate'])

    def test_equal_lead_syn_sim_equivalence(self):
        src=copy.deepcopy(self.p['sources'][:2]);src[0]['lead']=src[1]['lead']=1
        paths=np.tile([0.,30,70,20],(2,6))
        for model in ['rop','outl']:
            rows=[]
            for policy in app.POLICIES[:2]:
                if model=='rop':r=app.simulate_rop(self.p,src,policy,[.4,.6],[20,30],[55],paths)
                else:r=app.simulate_outl(self.p,src,policy,[.4,.6],[60],[13],paths)
                rows.append(r);self.check_balances(r)
            self.assertEqual(rows[0],rows[1])

    def test_all_model_policy_balances(self):
        paths=np.random.default_rng(1).poisson(40,(3,24));src=self.p['sources'][:2]
        for policy in app.POLICIES:
            ind=policy=='independent'
            self.check_balances(app.simulate_rop(self.p,src,policy,[.4,.6],[17,41] if ind else [20,30],[18,35] if ind else [55],paths))
            self.check_balances(app.simulate_outl(self.p,src,policy,[.4,.6],[32,55] if ind else [80],[9,23] if ind else [17],paths))

    def test_release_receipt_timing(self):
        src=copy.deepcopy(self.p['sources'][:2]);src[0]['lead']=.1;src[1]['lead']=1/30
        for policy in app.POLICIES[:2]:
            row=app.simulate_outl(self.p,src,policy,[.4,.6],[30],[1],[[300]],trace=True)
            orders=row['trace'][0]['orders']
            self.assertEqual([o['release_epoch'] for o in orders],[1,3] if policy=='synchronized' else [1,1])
            self.assertEqual([o['receipt_epoch'] for o in orders],[4,4] if policy=='synchronized' else [4,2])
            json.dumps(row,allow_nan=False)

    def test_zero_delay_does_not_retroactively_fill(self):
        src=copy.deepcopy(self.p['sources'][1]);src['lead']=0
        row=app.simulate_outl(self.p,[src],'synchronized',[1],[0],[1],[[30]],trace=True)
        self.assertEqual(row['trace'][0]['newly_unmet_kg'],1)
        self.assertEqual(row['trace'][0]['net_inventory_kg'],0)
        self.assertEqual(row['fill_rate'],0)

    def test_no_setup_for_zero_outl_order(self):
        src=copy.deepcopy(self.p['sources'][1]);src['lead']=0
        row=app.simulate_outl(self.p,[src],'synchronized',[1],[10],[1],[[30]+[0]*11])
        self.assertEqual(row['orders_per_year'],[30])
        self.assertEqual(row['costs']['Co'],30*src['co'])

    def test_multiple_rop_batches_each_incur_setup(self):
        src=copy.deepcopy(self.p['sources'][1]);src['lead']=0
        row=app.simulate_rop(self.p,[src],'synchronized',[1],[2],[0],[[300]],trace=True)
        self.assertEqual(row['trace'][0]['orders'][0]['quantity_kg'],12)
        # Thirty days contain 300 kg, plus one batch from the <= trigger.
        self.assertEqual(row['orders_per_year'],[151*12])
        self.assertEqual(row['costs']['Co'],151*12*src['co'])
        self.check_balances(row)

    def test_fill_rate_is_demand_weighted(self):
        src=copy.deepcopy(self.p['sources'][1]);src['lead']=.1
        paths=np.array([[30]*12,[300]*12])
        row=app.simulate_rop(self.p,[src],'synchronized',[1],[10],[5],paths)
        expected=1-sum(row['unmet_kg_replications'])/sum(row['demand_kg_replications'])
        self.assertAlmostEqual(row['fill_rate'],expected)
        self.assertNotAlmostEqual(row['fill_rate'],np.mean(row['fill_rate_replications']))

    def test_zero_demand_replication(self):
        row=app.simulate_outl(self.p,[self.p['sources'][1]],'synchronized',[1],[40],[15],[[0]*12,[30]*12])
        self.assertIsNone(row['fill_rate_replications'][0])
        self.assertEqual(row['fill_rate'],row['fill_rate_replications'][1])
        app.attach_uncertainty(row);json.dumps(row,allow_nan=False)

    def test_all_zero_demand_rejected(self):
        with self.assertRaisesRegex(ValueError,'No positive demand'):
            app.simulate_outl(self.p,[self.p['sources'][1]],'synchronized',[1],[40],[15],[[0]*12])

    def test_ind_candidate_parameters_are_free(self):
        src=self.p['sources'][:2];stats=app.demand_stats(self.p['demand'])
        for model in ['rop','outl']:
            par=app.candidate_parameters(self.p,src,'independent',model,stats,30,np.random.default_rng(1),(0.1,.9))
            if model=='rop':self.assertTrue(np.any(np.abs(np.diff(par['quantities']/par['shares'],axis=1))>1))
            else:self.assertTrue(np.any(par['reviews'][:,0]!=par['reviews'][:,1]))


class EndToEndTests(unittest.TestCase):
    def test_import_does_not_confuse_cycle_service_and_fill_rate(self):
        from openpyxl import Workbook
        wb=Workbook();sheet=wb.active;sheet.title='Parameters';wb.create_sheet('Sources')
        sheet.cell(6,1,'cycle_service_level');sheet.cell(6,2,.99)
        def imported():
            stream=io.BytesIO();wb.save(stream)
            return app.import_excel(base64.b64encode(stream.getvalue()).decode())
        result=imported()
        self.assertNotIn('service',result['costs']);self.assertTrue(result['import_warnings'])
        sheet.cell(7,1,'immediate_fill_rate_min');sheet.cell(7,2,.9)
        result=imported()
        self.assertEqual(result['costs']['service'],.9);self.assertFalse(result['import_warnings'])

    def test_six_stochastic_distribution_branches(self):
        for family, occurrence in itertools.product(['Normal','Poisson','Geometric'],[None,.3]):
            p=inputs();p['demand'].update(family=family,mu=100,sigma=15,lam=4,q=.1,unit=20,occurrence_probability=occurrence)
            result=app.solve(p)
            self.assertEqual(len(result['configurations']),9)
            self.assertEqual(result['model'],'Reorder Point' if occurrence is None else 'Order-Up-To-Level')
            self.assertEqual(result['evaluation'],'daily simulation')
            self.assertGreaterEqual(result['best']['fill_rate'],p['costs']['service'])
            json.dumps(result,allow_nan=False)

    def test_ww_matches_enumerated_schedules(self):
        d=[4,2,7,1];unit,setup,h=3,10,.2
        expected=[]
        for flags in itertools.product([False,True],repeat=3):
            starts=[0]+[i+1 for i,b in enumerate(flags) if b]+[4]
            value=unit*sum(d)+setup*(len(starts)-1)
            value+=sum(d[m]*(m-u)*unit*h/12 for u,v in zip(starts,starts[1:]) for m in range(u,v))
            expected.append(value)
        value,lots=app.wagner_whitin(d,unit,setup,h)
        self.assertAlmostEqual(value,min(expected));self.assertEqual(sum(l['quantity_kg'] for l in lots),sum(d))
        p=inputs();p.update(mode='known',monthly_demand=d)
        self.assertAlmostEqual(app.solve(p)['best']['annual_demand_kg'],sum(d)*3)

    def test_capacity_and_scrap(self):
        p=inputs();p['demand'].update(mu=100,sigma=0);p['costs'].update(scrap=100,**{'yield':.5});p['sources'][1]['capacity']=1000
        result=app.solve(p)
        for row in result['configurations']:
            if row['status']=='feasible':
                self.assertLessEqual(row['scrap_used_kg_per_year'],100+1e-8)
                for source in row['sources']:
                    cap=next(s['capacity'] for s in p['sources'] if s['id']==source['id'])
                    if cap is not None:self.assertLessEqual(source['annual_supply_kg'],cap+1e-8)
        self.assertNotEqual(result['configurations'][0]['status'],'feasible')

    def test_ten_sources_three_branches(self):
        for mode,occurrence in [('known',None),('uncertain',None),('uncertain',.3)]:
            p=inputs();p.update(mode=mode,monthly_demand=[30]*12);p['demand']['occurrence_probability']=occurrence
            for i in range(4,11):
                s=copy.deepcopy(p['sources'][1]);s.update(id=i,name=f'Supplier {i}');p['sources'].append(s)
            for s in p['sources']:s['capacity']=None if s['id']==10 else 0
            r=app.solve(p)
            self.assertEqual(len(r['configurations']),19 if mode=='known' else 37)
            self.assertEqual(r['best']['configuration'],'10')

    def test_validation(self):
        p=inputs();p['sources'][0]['lead']=.2;p['costs']['service']=.2;app.validate(p)
        p['sources'][0]['lead']=.201
        with self.assertRaises(ValueError):app.validate(p)
        p=inputs();p['costs']['service']=1
        with self.assertRaises(ValueError):app.validate(p)
        p=inputs();p['sources'].pop()
        with self.assertRaises(ValueError):app.validate(p)

    def test_reproducibility_and_input_preservation(self):
        p=inputs();before=copy.deepcopy(p)
        self.assertEqual(app.solve(p),app.solve(p));self.assertEqual(p,before)




class EvaluationOutcomeTests(unittest.TestCase):
    def test_resource_infeasibility_preserves_alternatives(self):
        for mode, occurrence in [('known',None),('uncertain',None),('uncertain',.3)]:
            p=inputs();p.update(mode=mode,monthly_demand=[30]*12)
            p['demand']['occurrence_probability']=occurrence
            for source in p['sources']:source['capacity']=0
            result=app.solve(p)
            self.assertIsNone(result['best'])
            self.assertEqual(len(result['configurations']),5 if mode=='known' else 9)
            self.assertTrue(all(r['status']!='feasible' for r in result['configurations']))
            json.dumps(result,allow_nan=False)

    def test_no_search_candidate_is_distinct_from_resource_infeasibility(self):
        from unittest.mock import patch
        p=inputs()
        with patch.object(app,'simulate_inventory_batch',return_value=[{'fill_rate':0.0}]*10):
            result=app.solve(p)
        self.assertIsNone(result['best'])
        self.assertTrue(all(r['status']=='no service-feasible candidate found in search' for r in result['configurations']))

    def test_validation_misses_preserve_selected_candidate_details(self):
        from unittest.mock import patch
        p=inputs()
        def metrics(*args,**kwargs):
            count=len(args[3])
            return [dict(fill_rate=1.0 if count>1 else 0.0,annual_cost_eur=100.0) for _ in range(count)]
        with patch.object(app,'simulate_inventory_batch',side_effect=metrics), patch.object(app,'attach_uncertainty'):
            result=app.solve(p)
        self.assertIsNone(result['best'])
        self.assertTrue(all(r['status']=='validation service target missed' for r in result['configurations']))
        self.assertTrue(all(r['sources'] for r in result['configurations']))

class StartupTests(unittest.TestCase):
    def test_occupied_port_gives_clear_error_without_opening_a_browser(self):
        import subprocess,sys
        from pathlib import Path
        with app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler) as occupied:
            result=subprocess.run([sys.executable,str(Path(app.__file__)), '--no-browser', '--port',str(occupied.server_address[1])],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,2)
        self.assertIn('An older app may still be running',result.stderr)
        self.assertIn('Do not reuse an old browser tab',result.stderr)

if __name__=='__main__':unittest.main()
