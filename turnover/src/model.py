"""Deterministic paper-account model. Decimal money, no broker or os imports."""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import date, timedelta
import hashlib,json
D=Decimal
ZERO=D('0')
def money(x):return D(str(x)).quantize(D('.01'),rounding=ROUND_HALF_UP)
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
class Calendar:
 def __init__(self,days):self.days=sorted(days)
 def session(self,d):return d in self.days
 def next(self,d):
  xs=[x for x in self.days if x>d]
  if not xs:raise RuntimeError('交易日历覆盖不足，停止交易，需更新官方日历')
  return xs[0]
 def previous(self,d):
  xs=[x for x in self.days if x<d]
  if not xs:raise RuntimeError('交易日历缺少前一交易日')
  return xs[-1]
 def monthend(self,d):return self.next(d)[:7]!=d[:7]
 def window(self,d,n):
  xs=[x for x in self.days if x<=d][-n:]
  if len(xs)!=n:raise RuntimeError('交易日历预热不足')
  return xs
 def delayed(self,d,n):
  xs=[x for x in self.days if x>=d]
  if len(xs)<=n:raise RuntimeError('公司行动到账日超过交易日历覆盖，需先更新日历')
  return xs[n]

def costs(value,sell,cfg):
 value=D(str(value));commission=max(D(cfg['minimum_commission']),money(value*D(cfg['sell_commission' if sell else 'buy_commission'])))
 return {'commission':str(commission),'stamp':str(money(value*D(cfg['stamp_sell']))) if sell else '0.00','transfer':str(money(value*D(cfg['transfer_rate'])))}
def cost_total(c):return sum((D(x) for x in c.values()),ZERO)
def new_state(cfg):return {'schema':1,'mode':'PAPER','cash':str(money(cfg['initial_cash'])),'positions':{},'orders':[],'blocked':[],'events':[],'phase_keys':[],'pending_sells':[],'entitlements':[],'receivables':[],'equity':[],'dividend_tax':'0.00','realized_pnl':'0.00','last_completed_close':None}
def clean_symbol(s):return s.startswith(('sh600','sh601','sh603','sh605','sz000','sz001','sz002','sz003'))
def eligible_name(n):return 'ST' not in n.upper() and '退' not in n

def select(day,history,universe,first_seen,cal,cfg):
 dates=cal.window(day,cfg['lookback']);rank=[];excluded={}
 for s,u in universe.items():
  reason=None
  if not clean_symbol(s) or not eligible_name(u['name']):reason='board_or_ST'
  elif not first_seen.get(s) or (date.fromisoformat(day)-date.fromisoformat(first_seen[s])).days<cfg['minimum_age_days']:reason='age_unknown_or_under_365d'
  else:
   values=[history.get(d,{}).get(s) for d in dates]
   if any(v is None or D(str(v))<=0 for v in values):reason='incomplete_positive_20_session_turnover'
  if reason:excluded[s]=reason;continue
  score=sum((D(str(v)) for v in values),ZERO)/D(len(values));rank.append((score,s))
 rank.sort();chosen=[{'symbol':s,'name':universe[s]['name'],'mean_turnover_pct':str(v)} for v,s in rank[:cfg['max_positions']]]
 if len(chosen)<cfg['max_positions']:raise RuntimeError('候选不足10只；停止生成交易信号，不把缺数据视为无信号')
 target=cfg['bootstrap_buy_date'] if day==cfg['bootstrap_signal_date'] else cal.next(day)
 return {'signal_date':day,'buy_date':target,'lookback_dates':dates,'targets':chosen,'eligible_count':len(rank),'excluded_count':len(excluded),'exclusions':excluded,'rule':'20_session_mean_turnover_ascending_equal_weight','bootstrap':day==cfg['bootstrap_signal_date']}

def validate_quote(q,d):
 if not q or q.get('date')!=d:return False
 try:return all(D(str(q.get(k,0)))>0 for k in ['open','close','prev_close','upper','lower']) and int(q.get('volume',0))>0
 except (InvalidOperation,ValueError,TypeError):return False

def blocked(st,day,s,side,why):st['blocked'].append({'date':day,'symbol':s,'side':side,'reason':why})
def apply_actions(st,day,cal,cfg):
 for ent in st['entitlements']:
  if ent['ex_date']!=day or ent.get('applied'):continue
  key='CA:'+ent['id']
  if key in st['events']:continue
  gross=money(D(ent['cash_per_share'])*ent['qty']);tax=money(gross*D(cfg['dividend_tax']));net=gross-tax;due=cal.delayed(day,cfg['corporate_action_delay_sessions'])
  st['receivables'].append({'due':due,'amount':str(net),'symbol':ent['symbol']});st['dividend_tax']=str(money(D(st['dividend_tax'])+tax))
  p=st['positions'].get(ent['symbol'])
  if p:
   bonus=int(D(str(ent['bonus_per_share']))*ent['qty']);p['qty']+=bonus;p['dividends']=str(money(D(p['dividends'])+net))
   if bonus:p['locks'].append({'due':due,'qty':bonus})
  ent['applied']=True;st['events'].append(key)
 for r in st['receivables']:
  if r['due']<=day and not r.get('paid'):st['cash']=str(money(D(st['cash'])+D(r['amount'])));r['paid']=True

def nav(st,quotes,day,opening=False):
 mv=ZERO;stale=[]
 for s,p in st['positions'].items():
  q=quotes.get(s);price=D(str(q['open' if opening else 'close'])) if validate_quote(q,day) else D(p['last_price'])
  if not validate_quote(q,day):stale.append(s)
  mv+=price*p['qty']
 receivable=sum((D(r['amount']) for r in st['receivables'] if not r.get('paid')),ZERO)
 return money(D(st['cash'])+mv+receivable),money(mv),money(receivable),stale

def run_open(st,day,signal,quotes,previous,cal,cfg,recorded_at):
 key=day+':open'
 if key in st['phase_keys']:return False
 if not cal.session(day):raise RuntimeError('非交易日')
 prev=cal.previous(day)
 if day>cfg['bootstrap_buy_date'] and st.get('last_completed_close')!=prev:raise RuntimeError('上个交易日账本未完成，禁止跳过账本直接买入')
 apply_actions(st,day,cal,cfg)
 if signal:
  if signal['buy_date']!=day or signal['signal_date']>=day:raise RuntimeError('信号日期错误或未来数据')
  if signal['signal_date']!=prev:raise RuntimeError('只允许前一交易日收盘后生成的冻结信号')
  value,_,_,_=nav(st,quotes,day,True);budget=value/D(cfg['max_positions'])
  for item in signal['targets']:
   s=item['symbol']
   if s in st['positions'] or len(st['positions'])>=cfg['max_positions']:continue
   q=quotes.get(s)
   if not validate_quote(q,day):blocked(st,day,s,'BUY','missing_stale_or_suspended');continue
   if not eligible_name(q['name']):blocked(st,day,s,'BUY','ST_or_delisting_at_entry');continue
   price=D(str(q['open']))
   if price>=D(str(q['upper'])):blocked(st,day,s,'BUY','open_at_upper_limit');continue
   old=previous.get(s)
   if not old or old['date']!=prev:blocked(st,day,s,'BUY','prior_volume_missing');continue
   cap=int(D(str(old['volume']))*D(cfg['prior_volume_participation'])/100)*100
   available=min(budget,D(st['cash']));qty=min(int(available/price/100)*100,cap)
   while qty>0 and money(price*qty)+cost_total(costs(price*qty,False,cfg))>available:qty-=100
   if qty<=0:blocked(st,day,s,'BUY','cash_lot_or_previous_volume_cap');continue
   v=money(price*qty);c=costs(v,False,cfg);paid=v+cost_total(c);st['cash']=str(money(D(st['cash'])-paid))
   st['positions'][s]={'name':q['name'],'qty':qty,'entry_date':day,'cost':str(paid),'dividends':'0.00','last_price':str(price),'locks':[]}
   st['orders'].append({'id':day+':BUY:'+s,'date':day,'execution_time':'09:30:00','recorded_at':recorded_at,'side':'BUY','symbol':s,'name':q['name'],'qty':qty,'price':str(price),'value':str(v),**c,'signal_hash':signal.get('frozen_hash',digest(signal)),'mode':'PAPER_OPEN_PRICE_MODEL'})
 st['phase_keys'].append(key)
 assert_account(st,cfg);return True

def run_close(st,day,quotes,actions,cal,cfg,recorded_at):
 key=day+':close'
 if key in st['phase_keys']:return False
 if day+':open' not in st['phase_keys']:raise RuntimeError('必须先处理当天开盘阶段，防止成交时序倒置')
 if cal.monthend(day):st['pending_sells']=sorted(set(st['pending_sells'])|set(st['positions']))
 for s in list(st['pending_sells']):
  if s not in st['positions']:st['pending_sells'].remove(s);continue
  p=st['positions'][s];q=quotes.get(s)
  if p['entry_date']>=day:blocked(st,day,s,'SELL','T_plus_1');continue
  if not validate_quote(q,day):blocked(st,day,s,'SELL','missing_stale_or_suspended');continue
  if any(x['due']>day for x in p['locks']):blocked(st,day,s,'SELL','bonus_shares_not_assumed_available');continue
  price=D(str(q['close']))
  if price<=D(str(q['lower'])):blocked(st,day,s,'SELL','close_at_lower_limit');continue
  v=money(price*p['qty']);c=costs(v,True,cfg);net=v-cost_total(c);st['cash']=str(money(D(st['cash'])+net));pnl=net-D(p['cost'])+D(p['dividends']);st['realized_pnl']=str(money(D(st['realized_pnl'])+pnl))
  st['orders'].append({'id':day+':SELL:'+s,'date':day,'execution_time':'15:00:00','recorded_at':recorded_at,'side':'SELL','symbol':s,'name':p['name'],'qty':p['qty'],'price':str(price),'value':str(v),**c,'realized_pnl':str(money(pnl)),'mode':'PAPER_CLOSE_PRICE_MODEL'})
  del st['positions'][s];st['pending_sells'].remove(s)
 for a in actions:
  if a['record_date']==day and a['symbol'] in st['positions']:
   ent=dict(a);ent['qty']=st['positions'][a['symbol']]['qty'];ent['applied']=False
   if not any(x['id']==ent['id'] for x in st['entitlements']):st['entitlements'].append(ent)
 for s,p in st['positions'].items():
  if validate_quote(quotes.get(s),day):p['last_price']=str(quotes[s]['close'])
 total,mv,receivable,stale=nav(st,quotes,day)
 st['equity'].append({'date':day,'cash':st['cash'],'market_value':str(mv),'dividend_receivable':str(receivable),'nav':str(total),'return_pct':str((total/D(str(cfg['initial_cash']))-1)*100),'positions':len(st['positions']),'stale_marks':stale,'recorded_at':recorded_at})
 st['last_completed_close']=day;st['phase_keys'].append(key);assert_account(st,cfg);return True

def assert_account(st,cfg):
 if D(st['cash'])<0 or len(st['positions'])>cfg['max_positions']:raise AssertionError('现金或最大持仓约束失败')
 ids=[x['id'] for x in st['orders']]
 if len(ids)!=len(set(ids)):raise AssertionError('重复成交')
 # Cash reconciles to individual orders and paid cash dividends exactly to cents.
 cash=D(str(cfg['initial_cash']))
 for o in st['orders']:cash+=(-D(o['value']) if o['side']=='BUY' else D(o['value']))-sum((D(o[k]) for k in ['commission','stamp','transfer']),ZERO)
 cash+=sum((D(r['amount']) for r in st['receivables'] if r.get('paid')),ZERO)
 if money(cash)!=money(st['cash']):raise AssertionError('逐笔现金流水不一致')
