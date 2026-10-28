import copy,json,unittest
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from ahpaper import core as c
from ahpaper.data import parse_quotes
CFG=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text());TZ=ZoneInfo('Asia/Shanghai')
def now(text='2026-10-29 09:32:00'):return datetime.fromisoformat(text).replace(tzinfo=TZ)
def quote(t=None,last=10,ask=10):
 t=t or now();return {'name':'测试','last':last,'ask':ask,'bid':9.99,'upper':11,'lower':9,'volume':10000000,'timestamp':t,'date':t.date().isoformat()}
def signal():return {'id':'x','signal_date':'2026-10-28','buy_date':'2026-10-29','selected':[{'symbol':'sh600036','A_volume_shares':100000000}],'attempted':False}
class PaperTests(unittest.TestCase):
 def test_calendar_and_bootstrap(self):
  self.assertEqual(c.next_session('2026-10-28',CFG),'2026-10-29');self.assertTrue(c.month_end('2026-10-30',CFG));self.assertEqual(c.next_session('2026-10-30',CFG),'2026-11-02');self.assertFalse(c.is_session('2026-10-01',CFG));self.assertFalse(c.month_end('2026-10-29',CFG))
  with self.assertRaises(ValueError):c.is_session('2027-01-04',CFG)
 def test_ceil_decile_ties(self):
  rows=[{'symbol':str(i).zfill(6),'premium':0.3} for i in range(131,-1,-1)];s,allrows=c.select(rows,CFG);self.assertEqual(len(s),14);self.assertEqual(s[0]['symbol'],'000000')
 def test_live_fill_idempotent_cash(self):
  st=c.initial_state(CFG);sig=signal();q={'sh600036':quote()};c.buy(st,sig,q,now(),CFG);self.assertEqual(st['positions']['sh600036']['qty']%100,0);self.assertGreater(st['cash'],0);n=len(st['events']);c.buy(st,sig,q,now(),CFG);self.assertEqual(len(st['events']),n);c.assert_account(st)
 def test_no_stale_or_upper_limit_fills(self):
  for q in [quote(t=now()-timedelta(minutes=5)),quote(last=11,ask=11),quote(ask=0)]:
   st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':q},now(),CFG);self.assertFalse(st['positions']);self.assertEqual(st['cash'],200000)
 def test_no_retroactive_open_fill(self):
  st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now('2026-10-29 10:01:00'),CFG);self.assertFalse(st['positions'])
 def test_no_close_without_precommitted_intent(self):
  st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now(),CFG);before=copy.deepcopy(st);c.settle_sell(st,{'sh600036':quote(now('2026-10-30 15:10:00'))},now('2026-10-30 15:15:00'),CFG);self.assertEqual(st['positions'],before['positions'])
 def test_sell_close_dedup_and_T1(self):
  st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now(),CFG);c.prepare_sell(st,now('2026-10-29 14:50:00'),CFG);self.assertFalse(st['sell_intents']);c.prepare_sell(st,now('2026-10-30 14:50:00'),CFG);q={'sh600036':quote(now('2026-10-30 15:10:00'),10.2,10.2)};c.settle_sell(st,q,now('2026-10-30 15:15:00'),CFG);self.assertFalse(st['positions']);n=len(st['events']);c.settle_sell(st,q,now('2026-10-30 15:16:00'),CFG);self.assertEqual(n,len(st['events']));c.assert_account(st)
 def test_lower_limit_keeps_position_pending(self):
  st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now(),CFG);c.prepare_sell(st,now('2026-10-30 14:50:00'),CFG);c.settle_sell(st,{'sh600036':quote(now('2026-10-30 15:10:00'),9,9)},now('2026-10-30 15:15:00'),CFG);self.assertTrue(st['positions']['sh600036']['exit_pending'])
 def test_dividend_record_ex_and_due(self):
  st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now(),CFG);qty=st['positions']['sh600036']['qty'];a=[{'symbol':'sh600036','record':'2026-10-29','ex':'2026-10-30','cash':.1,'bonus':0}];c.corporate_actions(st,a,now('2026-10-29 16:40:00'),CFG,True);c.corporate_actions(st,a,now('2026-10-30 09:31:00'),CFG);self.assertAlmostEqual(st['receivables'][0]['amount'],qty*.1*.8);before=copy.deepcopy(st);c.corporate_actions(st,a,now('2026-10-30 09:32:00'),CFG);self.assertEqual(st['cash'],before['cash']);self.assertEqual(len(st['receivables']),1);c.corporate_actions(st,a,now('2026-12-01 09:31:00'),CFG);self.assertFalse(st['receivables']);c.assert_account(st)
class PersistenceTests(unittest.TestCase):
 def test_quote_parser_exchange_limits_and_units(self):
  f=['0']*50;f[0]='1';f[1]='招商银行';f[3]='42';f[4]='41';f[5]='41.5';f[6]='1000';f[19]='42.01';f[30]='20261029093200';f[47]='45.10';f[48]='36.90'
  q=parse_quotes('v_sh600036="'+'~'.join(f)+'";')['sh600036'];self.assertEqual(q['volume'],100000);self.assertEqual(q['upper'],45.10);self.assertTrue(c.fresh(q,now(),CFG))
 def test_no_position_receivable_still_settles(self):
  import main as app
  import tempfile
  from unittest.mock import patch
  from ahpaper.data import save
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'data').mkdir();(root/'reports').mkdir();save(root/'config.json',CFG)
   st=c.initial_state(CFG);st['receivables']=[{'key':'prior','amount':100.,'due':'2026-10-29'}];save(root/'data/state.json',st)
   with patch.object(app,'ROOT',root):out=app.run(now(),'status')
   self.assertEqual(out['cash'],200100.);self.assertFalse(out['receivables'])
 def test_missing_preclose_job_does_not_fabricate_fill(self):
  import main as app
  import tempfile
  from unittest.mock import patch
  from ahpaper.data import save
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'data').mkdir();(root/'reports').mkdir();save(root/'config.json',CFG)
   st=c.initial_state(CFG);c.buy(st,signal(),{'sh600036':quote()},now(),CFG);save(root/'data/state.json',st)
   tm=now('2026-10-30 15:15:00')
   with patch.object(app,'ROOT',root),patch.object(app.d,'quotes',return_value={'sh600036':quote(tm)}),patch.object(app.d,'actions',return_value=[]):out=app.run(tm,'settle-close')
   self.assertTrue(out['positions']['sh600036']['exit_pending']);self.assertFalse(any(e['kind']=='SELL' for e in out['events']))
class WallClockTests(unittest.TestCase):
 def test_buy_uses_clock_after_quote_request(self):
  import main as app
  import tempfile
  from unittest.mock import patch
  from ahpaper.data import save
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'data').mkdir();(root/'reports').mkdir();save(root/'config.json',CFG)
   st=c.initial_state(CFG);st['signals']={'2026-10-28':signal()};save(root/'data/state.json',st)
   with patch.object(app,'ROOT',root),patch.object(app.d,'quotes',return_value={'sh600036':quote(now('2026-10-29 09:32:05'))}):out=app.run(now('2026-10-29 09:31:00'),'buy',clock_source=lambda:now('2026-10-29 09:32:10'))
   self.assertIn('sh600036',out['positions']);self.assertEqual(out['events'][0]['time'],now('2026-10-29 09:32:10').isoformat())
class CrossSectionTests(unittest.TestCase):
 def test_holiday_uses_both_legs_same_day(self):
  import tempfile
  from unittest.mock import patch
  from ahpaper import data as source
  tm=now('2026-10-28 16:40:00');pair={'SECURITY_CODE':'600036','SECURITY_NAME_ABBR':'招商银行','STR_CODEH':'03968.HK','LISTING_DATE':'2002-04-09'}
  aq=quote(now('2026-10-28 16:10:00'),last=50,ask=50);hq=quote(now('2026-10-27 16:08:00'),last=40,ask=40)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   with patch.object(source,'refresh_pairs',return_value=[pair]),patch.object(source,'quotes',return_value={'sh600036':aq,'hk03968':hq}),patch.object(source,'fx_rates',return_value={'2026-10-27':.9}),patch.object(source,'bars',return_value={'2026-10-27':{'close':36.,'volume':100000.}}):rows=source.signal_inputs(root,tm,CFG)
   self.assertAlmostEqual(rows[0]['premium'],0.);self.assertEqual(rows[0]['factor_date'],'2026-10-27');self.assertEqual(rows[0]['A_close'],36.)
 def test_no_silent_reduced_universe_on_missing_quote(self):
  import tempfile
  from unittest.mock import patch
  from ahpaper import data as source
  pair={'SECURITY_CODE':'600036','STR_CODEH':'03968.HK'}
  with tempfile.TemporaryDirectory() as t:
   with patch.object(source,'refresh_pairs',return_value=[pair]),patch.object(source,'quotes',return_value={}),patch.object(source,'fx_rates',return_value={'2026-10-28':.9}):
    with self.assertRaises(ValueError):source.signal_inputs(Path(t),now('2026-10-28 16:40:00'),CFG)
if __name__=='__main__':unittest.main()
