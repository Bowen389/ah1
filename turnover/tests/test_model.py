import unittest,json,copy
from pathlib import Path
from decimal import Decimal
from src import model,provider
ROOT=Path(__file__).resolve().parents[1]
class PaperChecks(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((ROOT/'config.json').read_text());self.cal=model.Calendar(json.loads((ROOT/'data/calendar.json').read_text())['days']);self.symbols=['sh600%03d'%i for i in range(10)];self.st=model.new_state(self.cfg)
  self.sig={'signal_date':'2026-10-28','buy_date':'2026-10-29','targets':[{'symbol':s} for s in self.symbols]}
 def quotes(self,day):return {s:{'date':day,'name':'样例公司','open':'10','close':'10','prev_close':'10','volume':10000000,'upper':'11','lower':'9'} for s in self.symbols}
 def opened(self):
  model.run_open(self.st,'2026-10-29',self.sig,self.quotes('2026-10-29'),self.quotes('2026-10-28'),self.cal,self.cfg,'TEST')
 def test_minimum_commission_and_user_stamp(self):
  self.assertEqual(model.costs(20000,True,self.cfg),{'commission':'5.00','stamp':'2.00','transfer':'0.20'})
  self.assertEqual(model.costs(20000,False,self.cfg)['stamp'],'0.00')
 def test_calendar_bootstrap_and_month_end(self):
  self.assertTrue(self.cal.monthend('2026-10-30'));self.assertFalse(self.cal.monthend('2026-10-29'));self.assertEqual(self.cal.next('2026-10-30'),'2026-11-02')
  self.assertFalse(self.cal.session('2026-10-03'))
  with self.assertRaises(RuntimeError):self.cal.next('2026-12-31')
 def test_idempotent_open_and_cash_reconciliation(self):
  self.opened();old=copy.deepcopy(self.st);self.opened();self.assertEqual(self.st,old);self.assertEqual(len(self.st['orders']),10);model.assert_account(self.st,self.cfg)
  self.assertGreaterEqual(Decimal(self.st['cash']),0)
  self.assertTrue(all(p['qty']%100==0 for p in self.st['positions'].values()))
 def test_open_upper_limit_ST_and_stale_quotes_blocked(self):
  q=self.quotes('2026-10-29');q[self.symbols[0]]['open']='11';q[self.symbols[1]]['date']='2026-10-28';q[self.symbols[2]]['name']='*STTEST'
  model.run_open(self.st,'2026-10-29',self.sig,q,self.quotes('2026-10-28'),self.cal,self.cfg,'TEST')
  self.assertEqual(len(self.st['positions']),7);self.assertEqual(len(self.st['blocked']),3)
 def test_T_plus_one(self):
  self.opened();self.st['pending_sells']=list(self.st['positions']);model.run_close(self.st,'2026-10-29',self.quotes('2026-10-29'),[],self.cal,self.cfg,'TEST')
  self.assertEqual(len(self.st['positions']),10);self.assertTrue(all(x['reason']=='T_plus_1' for x in self.st['blocked']))
 def test_lower_limit_sale_deferred(self):
  self.opened();model.run_close(self.st,'2026-10-29',self.quotes('2026-10-29'),[],self.cal,self.cfg,'TEST');model.run_open(self.st,'2026-10-30',None,self.quotes('2026-10-30'),self.quotes('2026-10-29'),self.cal,self.cfg,'TEST')
  q=self.quotes('2026-10-30');q[self.symbols[0]]['close']='9';model.run_close(self.st,'2026-10-30',q,[],self.cal,self.cfg,'TEST')
  self.assertEqual(list(self.st['positions']),[self.symbols[0]]);self.assertEqual(self.st['pending_sells'],[self.symbols[0]])
  model.assert_account(self.st,self.cfg)
 def test_future_signal_and_missing_prior_day_blocked(self):
  bad=dict(self.sig);bad['signal_date']='2026-10-29'
  with self.assertRaises(RuntimeError):model.run_open(self.st,'2026-10-29',bad,self.quotes('2026-10-29'),{},self.cal,self.cfg,'TEST')
  self.st=model.new_state(self.cfg)
  with self.assertRaises(RuntimeError):model.run_open(self.st,'2026-10-30',None,self.quotes('2026-10-30'),{},self.cal,self.cfg,'TEST')
 def test_dividend_not_paid_or_applied_twice(self):
  self.opened();s=self.symbols[0];a={'id':s+':2026-10-30','symbol':s,'record_date':'2026-10-29','ex_date':'2026-10-30','cash_per_share':'0.10','bonus_per_share':'0.10'}
  model.run_close(self.st,'2026-10-29',self.quotes('2026-10-29'),[a],self.cal,self.cfg,'TEST');model.run_open(self.st,'2026-10-30',None,self.quotes('2026-10-30'),self.quotes('2026-10-29'),self.cal,self.cfg,'TEST')
  q=self.st['positions'][s]['qty'];self.assertEqual(q,2090);self.assertEqual(self.st['dividend_tax'],'38.00');self.assertEqual(self.st['receivables'][0]['amount'],'152.00')
  model.apply_actions(self.st,'2026-10-30',self.cal,self.cfg);self.assertEqual(self.st['positions'][s]['qty'],q)
  model.apply_actions(self.st,self.st['receivables'][0]['due'],self.cal,self.cfg);cash=self.st['cash'];model.apply_actions(self.st,self.st['receivables'][0]['due'],self.cal,self.cfg);self.assertEqual(cash,self.st['cash']);model.assert_account(self.st,self.cfg)
 def test_factor_does_not_use_future_data(self):
  ds=self.cal.window('2026-10-28',20);h={d:{s:str(i+1) for i,s in enumerate(self.symbols)} for d in ds};u={s:{'name':'样例公司'} for s in self.symbols};first={s:'2020-01-01' for s in u}
  a=model.select('2026-10-28',h,u,first,self.cal,self.cfg);h['2026-10-29']={s:100000 for s in u};b=model.select('2026-10-28',h,u,first,self.cal,self.cfg);self.assertEqual(a,b)
 def test_tencent_parser_units_dates_limits(self):
  raw=(ROOT/'tests/tencent_sample.txt').read_text();q=provider.parse_tencent(raw)['sh600000'];self.assertEqual(q['date'],'2026-10-28');self.assertEqual(q['volume'],64727600);self.assertEqual(q['upper'],'10.80');self.assertEqual(q['lower'],'8.84')
if __name__=='__main__':unittest.main()
