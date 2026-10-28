"""AH lowest-decile paper portfolio. No broker keys or live-order API exists."""
import argparse,hashlib,json,math,sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from ahpaper import core as c, data as d
ROOT=Path(__file__).resolve().parent;TZ=ZoneInfo('Asia/Shanghai')

def mark_and_report(state,quotes,now,cfg,after_close=False):
 today=now.date().isoformat();mv=0.;stale=[]
 for s,p in state['positions'].items():
  q=quotes.get(s)
  if q and q['date']==today and (not after_close or q['timestamp'].strftime('%H:%M')>='15:00'):state['marks'][s]={'date':today,'price':q['last']}
  else:stale.append(s)
  mark=state['marks'].get(s)
  if mark is None:
   mark={'date':p['entry_date'],'price':p['cost']/p['qty']};stale.append(s)
  mv+=p['qty']*mark['price']
 receivable=sum(r['amount'] for r in state['receivables']);nav=c.money(state['cash']+mv+receivable)
 state['nav_history'][today]={'asof':now.isoformat(),'cash':state['cash'],'market_value':c.money(mv),'receivable':c.money(receivable),'nav':nav,'return':nav/state['initial_cash']-1,'positions':len(state['positions']),'stale_marks':sorted(set(stale)),'final_close_mark':after_close and not stale}
 state['last_run']=now.isoformat();c.assert_account(state)
 queued=[x for x in state['signals'].values() if not x.get('attempted')]
 lines=['# AH溢价最低10% · 20万元模拟账户','',f'更新时间：{now.isoformat()}（北京时间）','', '**PAPER_ONLY：没有真实券商订单。**','',f'- 初始资金：{state["initial_cash"]:,.2f}元',f'- 现金：{state["cash"]:,.2f}元',f'- 持仓市值：{mv:,.2f}元',f'- 应收红利：{receivable:,.2f}元',f'- 总资产：{nav:,.2f}元',f'- 累计收益：{nav/state["initial_cash"]-1:.2%}',f'- 持仓数：{len(state["positions"])}',f'- 未更新收盘估值：{", ".join(sorted(set(stale))) or "无"}','', '## 持仓','', '|代码|名称|数量|买入日|参考成本|','|---|---|---:|---|---:|']
 for s,p in state['positions'].items():lines.append(f'|{s}|{p["name"]}|{p["qty"]}|{p["entry_date"]}|{p["cost"]:.2f}|')
 lines+=['','## 待执行候选','']
 for sig in queued:
  lines+=[f'### {sig["signal_date"]}信号 → {sig["buy_date"]}模拟买入','',f'合格样本{sig["eligible_count"]}只，向上取整选最低10%共{len(sig["selected"])}只。','', '|代码|名称|H代码|溢价率|参考均分预算|','|---|---|---|---:|---:|']
  for r in sig['selected']:lines.append(f'|{r["symbol"]}|{r["name"]}|{r["H_symbol"]}|{r["premium"]:.2%}|{nav/len(sig["selected"]):.2f}|')
 lines+=['','## 最近事件','', '```json', json.dumps(state['events'][-15:],ensure_ascii=False,indent=2),'```','', '实时报价不新鲜、停牌、涨跌停或资金不足会拒绝模拟成交；不会补记过去的开盘价。收盘卖出为提前登记意图后的日线收盘近似。','公司行动现金/送转可用日缺失，保守假设20交易日延迟、20%红利税；不同于券商实际结算。']
 text='\n'.join(lines)+'\n';(ROOT/'reports/latest.md').write_text(text);(ROOT/f'reports/{today}.md').write_text(text);d.save(ROOT/'data/status.json',state['nav_history'][today])

def run(now,phase='auto',clock_source=None):
 clock_source=clock_source or (lambda:now)
 cfg=d.load(ROOT/'config.json');assert cfg['mode']=='PAPER_ONLY'
 p=ROOT/'data/state.json';state=d.load(p) if p.exists() else c.initial_state(cfg);today=now.date().isoformat();clock=now.strftime('%H:%M')
 if not c.is_session(today,cfg):return state
 if phase=='auto':
  phase='buy' if '09:30'<=clock<'10:00' else ('prepare-sell' if '14:45'<=clock<'15:00' else ('scan' if clock>='16:25' else ('settle-close' if clock>='15:05' else 'status')))
 if phase=='scan' and clock<'16:25':raise ValueError('AH收盘筛选最早北京时间16:25，避免香港延迟报价尚未收盘')
 quotes={};corp=[]
 if state['positions']:
  quotes=d.quotes(state['positions']);corp=d.actions(state['positions'],now)
 now=clock_source();clock=now.strftime('%H:%M')
 if now.date().isoformat()!=today:raise ValueError('任务跨日，停止执行')
 c.corporate_actions(state,corp,now,cfg)
 if phase=='buy':
  if not ('09:30'<=clock<'10:00'):raise ValueError('已错过模拟买入窗口，不倒填开盘成交')
  for sig in state['signals'].values():
   if not sig.get('attempted') and sig['buy_date']==today:
    symbols=[r['symbol'] for r in sig['selected']]+list(state['positions']);quotes=d.quotes(symbols);now=clock_source();clock=now.strftime('%H:%M')
    if now.date().isoformat()!=today:raise ValueError('行情请求跨日，停止执行')
    d.save(ROOT/f'data/raw/buy_quotes_{today}.json',{'captured_at':now.isoformat(),'quotes':quotes})
    c.buy(state,sig,quotes,now,cfg)
 elif phase=='prepare-sell':c.prepare_sell(state,now,cfg)
 elif phase in ['settle-close','scan']:
  if clock<'15:05':raise ValueError('未到收盘结算时间')
  if quotes:d.save(ROOT/f'data/raw/close_quotes_{today}.json',{'captured_at':now.isoformat(),'quotes':quotes})
  c.settle_sell(state,quotes,now,cfg)
  # Missing the pre-close job never creates an imaginary past close order.
  if c.month_end(today,cfg) or today==cfg['bootstrap_exit_date']:
   for s,position in state['positions'].items():
    if position['entry_date']<today and not position.get('exit_pending'):
     position['exit_pending']=True;c.event(state,'MISSED_CLOSE_INTENT',now,symbol=s,reason='no_preclose_intent_retry_next_session')
  c.corporate_actions(state,corp,now,cfg,after_close=True)
  should_scan=today==cfg['bootstrap_signal_date'] or c.month_end(today,cfg)
  if phase=='scan' and should_scan and today not in state['signals']:
   rows=d.signal_inputs(ROOT,now,cfg);now=clock_source();clock=now.strftime('%H:%M');selected,eligible=c.select(rows,cfg);buydate=cfg['bootstrap_buy_date'] if today==cfg['bootstrap_signal_date'] else c.next_session(today,cfg)
   assert buydate>today and c.is_session(buydate,cfg)
   sig={'id':'AH10_'+today,'signal_date':today,'generated_at':now.isoformat(),'buy_date':buydate,'eligible_count':len(eligible),'selection_fraction':.1,'selected':selected,'attempted':False,'bootstrap_exception':today==cfg['bootstrap_signal_date'],'factor_date':selected[0]['factor_date']}
   sig['sha256']=hashlib.sha256(json.dumps(sig,ensure_ascii=False,sort_keys=True).encode()).hexdigest();state['signals'][today]=sig;d.save(ROOT/f'data/signals/{today}.json',{'signal':sig,'eligible_sorted':eligible,'excluded':[r for r in rows if r.get('excluded')]});c.event(state,'SIGNAL_FROZEN',now,id=sig['id'],count=len(selected),eligible=len(eligible),buy_date=buydate,sha256=sig['sha256'])
 # Expired signals are audited, never executed retrospectively.
 for sig in state['signals'].values():
  if not sig.get('attempted') and (sig['buy_date']<today or (sig['buy_date']==today and clock>='10:00')):
   sig['attempted']=True;sig['expired']=True;c.event(state,'BUY_WINDOW_MISSED',now,signal=sig['id'])
 mark_and_report(state,quotes,now,cfg,phase in ['scan','settle-close']);d.save(p,state)
 err=ROOT/'reports/error_latest.md'
 if err.exists():err.unlink()
 return state

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',choices=['auto','scan','buy','prepare-sell','settle-close','status'],default='auto');args=ap.parse_args();now=datetime.now(TZ)
 try:
  state=run(now,args.phase,clock_source=lambda:datetime.now(TZ));print(json.dumps({'time':now.isoformat(),'mode':'PAPER_ONLY','cash':state['cash'],'positions':len(state['positions']),'signals':len(state['signals'])},ensure_ascii=False))
 except Exception as ex:
  text=f'# 模拟流程失败\n\n时间：{now.isoformat()}\n\n{type(ex).__name__}: {ex}\n\n未将过期行情补记成真实成交，请查看Actions日志。\n';(ROOT/'reports/error_latest.md').write_text(text);print(text,file=sys.stderr);raise
if __name__=='__main__':main()
