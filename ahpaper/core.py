from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math

def money(x): return round(float(x)+1e-10, 2)
def price(x): return float(Decimal(str(x)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
def is_session(day, cfg):
 d=date.fromisoformat(str(day)[:10])
 if d.isoformat()>cfg['calendar_valid_through'] or d.year!=2026: raise ValueError('交易日历已过期，更新官方休市表后才能执行')
 return d.weekday()<5 and d.isoformat() not in cfg['holidays_2026']
def next_session(day,cfg):
 d=date.fromisoformat(str(day)[:10])+timedelta(days=1)
 while not is_session(d,cfg): d+=timedelta(days=1)
 return d.isoformat()
def month_end(day,cfg): return is_session(day,cfg) and next_session(day,cfg)[:7]!=str(day)[:7]
def add_sessions(day,n,cfg):
 for _ in range(n): day=next_session(day,cfg)
 return str(day)[:10]
def costs(value,sell,cfg):
 return {'commission':money(max(cfg['minimum_commission'],value*cfg['commission_rate'])), 'stamp':money(value*cfg['sell_stamp']) if sell else 0., 'transfer':money(value*cfg['transfer_fee'])}
def initial_state(cfg):
 return {'schema':1,'mode':'PAPER_ONLY','initial_cash':cfg['initial_cash'],'cash':float(cfg['initial_cash']),'positions':{},'signals':{},'events':[],'sell_intents':{},'claims':{},'receivables':[],'actions_applied':[],'marks':{},'nav_history':{},'last_run':None}
def event(state,kind,now,**fields): state['events'].append({'id':len(state['events'])+1,'kind':kind,'time':now.isoformat(),**fields})
def assert_account(state):
 assert state['mode']=='PAPER_ONLY' and state['cash']>=-.005
 assert all(p['qty']>0 for p in state['positions'].values())
 expected=state['initial_cash']
 for x in state['events']:
  if x['kind']=='BUY': expected-=x['value']+sum(x['fees'].values())
  elif x['kind']=='SELL': expected+=x['value']-sum(x['fees'].values())
  elif x['kind']=='DIVIDEND_PAID':expected+=x['amount']
 assert abs(expected-state['cash'])<.02, (expected,state['cash'])
def select(rows,cfg):
 eligible=[r for r in rows if not r.get('excluded')]
 eligible.sort(key=lambda x:(round(x['premium'],12),x['symbol']))
 n=math.ceil(len(eligible)*cfg['selection_fraction'])
 if not n: raise ValueError('无有效AH股票，禁止生成空组合替代真实信号')
 return eligible[:n],eligible

def fresh(q,now,cfg):
 return q['date']==now.date().isoformat() and 0<q['last'] and 0<=(now-q['timestamp']).total_seconds()<=cfg['quote_max_age_seconds']
def opening_quote(q,now):
 try:
  return q['date']==now.date().isoformat() and q['timestamp'].date()==now.date() and q['timestamp'].strftime('%H:%M')>='09:30' and q['timestamp']<=now and math.isfinite(q['open']) and q['open']>0 and q['volume']>0
 except (KeyError,ValueError,TypeError):return False
def buy(state,signal,quotes,now,cfg):
 today=now.date().isoformat();clock=now.strftime('%H:%M')
 if signal.get('attempted') or signal['buy_date']!=today:return
 if not (cfg['buy_window_start']<=clock<cfg['buy_window_end']):return
 if signal['signal_date']>=today:raise ValueError('信号必须早于买入日')
 nav=state['cash']+sum(r['amount'] for r in state['receivables'])
 for s,p in state['positions'].items():
  if s not in quotes or not opening_quote(quotes[s],now):raise ValueError('持仓缺少当日开盘价，停止本轮买入')
  nav+=p['qty']*quotes[s]['open']
 budget=nav/len(signal['selected'])
 for r in signal['selected']:
  s=r['symbol'];q=quotes.get(s);reason=None
  if s in state['positions'] or len(state['positions'])>=len(signal['selected']):reason='existing_position_or_slots_full'
  elif not q or not opening_quote(q,now):reason='missing_or_wrong_day_open'
  elif 'ST' in q['name'].upper() or '退' in q['name']:reason='current_ST_or_delisting'
  elif q['open']<=0:reason='missing_open_price'
  elif q['upper']<=0 or q['lower']<=0:reason='missing_exchange_limit_prices'
  elif q['open']>=q['upper']-.005:reason='open_at_upper_limit'
  if reason:event(state,'REJECT',now,symbol=s,side='BUY',reason=reason,signal=signal['id']);continue
  px=price(q['open'])
  qty=min(math.floor(min(budget,state['cash'])/px/cfg['lot'])*cfg['lot'],math.floor(r['A_volume_shares']*cfg['participation_previous_volume']/cfg['lot'])*cfg['lot'])
  while qty>0:
   fee=costs(qty*px,False,cfg)
   if qty*px+sum(fee.values())<=min(budget,state['cash'])+.0001:break
   qty-=cfg['lot']
  if qty<=0:event(state,'REJECT',now,symbol=s,side='BUY',reason='cash_lot_or_volume_cap',signal=signal['id']);continue
  value=money(qty*px);state['cash']=money(state['cash']-value-sum(fee.values()));state['positions'][s]={'name':q['name'],'qty':qty,'entry_date':today,'cost':money(value+sum(fee.values())),'locked_until':None}
  event(state,'BUY',now,symbol=s,qty=qty,price=px,value=value,fees=fee,quote_time=q['timestamp'].isoformat(),signal=signal['id'],model='opening_price_model',execution_time=today+'T09:30:00+08:00',observed_at=now.isoformat(),price_source='Tencent official open',same_day_delayed_recording=clock>='09:40')
 signal['attempted']=True;signal['attempt_time']=now.isoformat();assert_account(state)

def prepare_sell(state,now,cfg):
 today=now.date().isoformat();clock=now.strftime('%H:%M')
 if not ('14:45'<=clock<'15:00'):return
 due=month_end(today,cfg) or today==cfg['bootstrap_exit_date']
 for s,p in state['positions'].items():
  if (due or p.get('exit_pending')) and p['entry_date']<today:
   key=today+':'+s
   if key not in state['sell_intents']:
    state['sell_intents'][key]={'symbol':s,'date':today,'qty':p['qty'],'created_at':now.isoformat(),'done':False};p['exit_pending']=True;event(state,'SELL_INTENT',now,symbol=s,qty=p['qty'])

def settle_sell(state,quotes,now,cfg):
 today=now.date().isoformat()
 if now.strftime('%H:%M')<'15:05':raise ValueError('尚未收盘，禁止用收盘价结算')
 for key,t in state['sell_intents'].items():
  if t['done'] or t['date']!=today:continue
  assert t['created_at'][:10]==today and t['created_at'][11:16]<'15:00'
  s=t['symbol'];p=state['positions'].get(s);q=quotes.get(s)
  if not p: t['done']=True;continue
  reason=None
  if not q or q['date']!=today or q['timestamp'].strftime('%H:%M')<'15:00':reason='missing_today_close'
  elif p['entry_date']>=today:reason='T1'
  elif p.get('locked_until') and p['locked_until']>today:reason='bonus_shares_locked'
  elif q['volume']<=0 or q['lower']<=0:reason='suspended_or_missing_limits'
  elif q['last']<=q['lower']+.005:reason='at_lower_limit'
  elif t['qty']>q['volume']*cfg['participation_previous_volume']:reason='daily_volume_cap'
  if reason:
   event(state,'REJECT',now,symbol=s,side='SELL',reason=reason);t['done']=True;continue
  qty=t['qty'];px=max(q['lower'],price(q['last']*(1-cfg['slippage'])));value=money(qty*px);fee=costs(value,True,cfg);state['cash']=money(state['cash']+value-sum(fee.values()));p['qty']-=qty
  if not p['qty']:del state['positions'][s]
  t['done']=True;event(state,'SELL',now,symbol=s,qty=qty,price=px,value=value,fees=fee,model='precommitted_close_intent_daily_close_proxy',intent_created_at=t['created_at'])
 assert_account(state)

def corporate_actions(state,actions,now,cfg,after_close=False):
 today=now.date().isoformat()
 for a in actions:
  s=a['symbol'];key=s+':'+a['record']+':'+a['ex']
  if after_close and a['record']==today and s in state['positions'] and key not in state['claims']:
   state['claims'][key]={'qty':state['positions'][s]['qty'],**a};event(state,'ENTITLEMENT',now,symbol=s,qty=state['positions'][s]['qty'],record=a['record'],ex=a['ex'])
 for key,a in state['claims'].items():
  if a['ex']<=today and key not in state['actions_applied']:
   net=money(a['qty']*a['cash']*.8);due=add_sessions(a['ex'],20,cfg)
   if net:state['receivables'].append({'key':key,'amount':net,'due':due})
   bonus=math.floor(a['qty']*a['bonus']+1e-9);p=state['positions'].get(a['symbol'])
   if bonus:
    if not p:raise ValueError('送转权益持仓已卖出，需要人工核对，停止记账')
    p['qty']+=bonus;p['locked_until']=due
   state['actions_applied'].append(key);event(state,'EX_DIVIDEND',now,symbol=a['symbol'],net_receivable=net,bonus=bonus,assumed_due=due)
 keep=[]
 for r in state['receivables']:
  if r['due']<=today:state['cash']=money(state['cash']+r['amount']);event(state,'DIVIDEND_PAID',now,amount=r['amount'],key=r['key'])
  else:keep.append(r)
 state['receivables']=keep;assert_account(state)

