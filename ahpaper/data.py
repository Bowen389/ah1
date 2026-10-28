import concurrent.futures as futures
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import json,re,urllib.request,urllib.parse
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Asia/Shanghai')

def request(url,binary=False):
 last=None
 for _ in range(2):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://gu.qq.com/'})
   with urllib.request.urlopen(req,timeout=18) as r:b=r.read()
   return b if binary else json.loads(b)
  except Exception as ex:last=ex
 raise RuntimeError(f'行情请求失败: {url.split("?")[0]}: {type(last).__name__}: {last}')
def save(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str));tmp.replace(path)
def load(path):return json.loads(path.read_text())
def refresh_pairs(root,now,cfg):
 def page(n):
  args={'reportName':'RPT_F10_ORG_BASICINFO','columns':'SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,STR_CODEA,STR_CODEH,LISTING_DATE,LISTING_STATE','pageSize':500,'pageNumber':n}
  j=request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(args))
  if not j.get('success') or not j.get('result'):raise ValueError('AH对应关系接口不完整')
  return j
 first=page(1);pages=first['result']['pages'];rows=first['result']['data']
 with futures.ThreadPoolExecutor(max_workers=10) as p:
  for j in p.map(page,range(2,pages+1)):rows.extend(j['result']['data'])
 pairs={}
 for x in rows:
  c=x['SECURITY_CODE']
  if len(c)==6 and c.startswith(tuple(cfg['mainboard_prefixes'])) and x.get('STR_CODEH') and x.get('STR_CODEA')==x['SECUCODE'] and x['LISTING_STATE']=='0':pairs[c]=x
 seed=load(root/'data/pairs_seed.json');assert len(pairs)>=len(seed)*.95,'AH列表覆盖异常下降'
 result=sorted(pairs.values(),key=lambda x:x['SECURITY_CODE']);save(root/'data/pairs_current.json',{'asof':now.isoformat(),'source':'Eastmoney RPT_F10_ORG_BASICINFO STR_CODEA/STR_CODEH','pairs':result});return result

def bars(symbol):
 j=request('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urllib.parse.urlencode({'param':f'{symbol},day,,,12,'}))
 if j.get('code')!=0 or not isinstance(j.get('data'),dict):raise ValueError('日线接口错误 '+symbol)
 data=j['data'].get(symbol,{}).get('day',[])
 if not data:raise ValueError('日线为空 '+symbol)
 result={}
 for r in data:
  d=r[0];o,c,h,l,v=map(float,r[1:6])
  if min(o,c,h,l)<=0 or h<max(o,c,l) or l>min(o,c,h) or v<0:raise ValueError('日线校验失败 '+symbol)
  result[d]={'open':o,'close':c,'high':h,'low':l,'volume':v*(100 if symbol[:2] in ['sh','sz'] else 1)}
 return result

def parse_quotes(text):
 out={}
 for s,body in re.findall(r'v_([a-z]+\d+)="([^"]*)";',text):
  f=body.split('~')
  if len(f)<49:continue
  raw=f[30]
  try:t=datetime.strptime(raw,'%Y%m%d%H%M%S').replace(tzinfo=TZ)
  except ValueError:
   try:t=datetime.strptime(raw,'%Y/%m/%d %H:%M:%S').replace(tzinfo=TZ)
   except ValueError:continue
  def n(i):
   try:return float(f[i])
   except (ValueError,IndexError):return 0.
  out[s]={'name':f[1],'last':n(3),'previous_close':n(4),'open':n(5),'volume':n(6)*(100 if s[:2] in ['sh','sz'] else 1),'bid':n(9),'ask':n(19),'ask_qty':n(20)*100,'timestamp':t,'date':t.date().isoformat(),'upper':n(47) if s[:2] in ['sh','sz'] else 0.,'lower':n(48) if s[:2] in ['sh','sz'] else 0.}
 return out

def quotes(symbols):
 out={};symbols=sorted(set(symbols))
 for i in range(0,len(symbols),50):
  text=request('https://qt.gtimg.cn/q='+','.join(symbols[i:i+50]),True).decode('gb18030');out.update(parse_quotes(text))
 if symbols and len(out)<len(symbols)*.95:raise ValueError('实时报价覆盖不足95%')
 return out
class FXParser(HTMLParser):
 def __init__(self):super().__init__();self.rows=[];self.row=[];self.cell=None
 def handle_starttag(self,t,a):
  if t=='tr':self.row=[]
  if t=='td':self.cell=[]
 def handle_data(self,s):
  if self.cell is not None:self.cell.append(s)
 def handle_endtag(self,t):
  if t=='td' and self.cell is not None:self.row.append(''.join(self.cell).strip());self.cell=None
  if t=='tr' and self.row:self.rows.append(self.row)
def fx_rates(now):
 args={'startDate':(now.date()-timedelta(days=20)).isoformat(),'endDate':now.date().isoformat(),'pageSize':1000}
 raw=request('https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do?'+urllib.parse.urlencode(args),True).decode('utf-8');p=FXParser();p.feed(raw);out={}
 for r in p.rows:
  if len(r)>=5 and re.fullmatch(r'\d{4}-\d\d-\d\d',r[0]):out[r[0]]=float(r[4])/100
 if not out or not all(.5<v<1.5 for v in out.values()):raise ValueError('港元中间价缺失或单位错误')
 return out

def signal_inputs(root,now,cfg):
 current=root/'data/pairs_current.json'
 if current.exists() and load(current).get('asof','')[:10]==now.date().isoformat():pairs=load(current)['pairs']
 else:pairs=refresh_pairs(root,now,cfg)
 symbols=[]
 for x in pairs:
  code=x['SECURITY_CODE'];symbols.extend([('sh' if code.startswith('6') else 'sz')+code,'hk'+x['STR_CODEH'].split('.')[0]])
 q=quotes(symbols);today=now.date().isoformat();fx=fx_rates(now)
 missing=set(symbols)-set(q)
 if missing:raise ValueError('截面报价缺失，冻结筛选: '+str(sorted(missing)))
 a_dates={r['date'] for s,r in q.items() if s[:2] in ['sh','sz'] and r['volume']>0 and r['last']>0}
 h_dates={r['date'] for s,r in q.items() if s.startswith('hk') and r['volume']>0 and r['last']>0 and r['date']<=today}
 if today not in a_dates or not h_dates:raise ValueError('当天A股或有效H股收盘报价未就绪')
 anchor=max(h_dates)
 if (now.date()-date.fromisoformat(anchor)).days>7:raise ValueError('H股报价整体过期')
 # On an HK holiday both price legs must use the same previous common day.
 history={}
 if anchor!=today:
  with futures.ThreadPoolExecutor(max_workers=12) as pool:
   jobs={pool.submit(bars,s):s for s in symbols if s[:2] in ['sh','sz']}
   for f in futures.as_completed(jobs):history[jobs[f]]=f.result()
 fxdates=sorted(x for x in fx if x<=anchor)
 if not fxdates or (date.fromisoformat(anchor)-date.fromisoformat(fxdates[-1])).days>7:raise ValueError('汇率过期')
 rate=fx[fxdates[-1]];rows=[]
 for x in pairs:
  code=x['SECURITY_CODE'];symbol=('sh' if code.startswith('6') else 'sz')+code;h='hk'+x['STR_CODEH'].split('.')[0];a=q[symbol];hq=q[h];reason=None
  if a['date']!=today or a['timestamp'].strftime('%H:%M')<'15:00':reason='A_today_final_close_not_ready'
  elif 'ST' in a['name'].upper() or '退' in a['name']:reason='ST_or_delisting'
  elif (now.date()-date.fromisoformat(x['LISTING_DATE'][:10])).days<cfg['minimum_A_listing_days']:reason='A_listing_under_one_year'
  elif a['volume']<=0:reason='A_suspended'
  elif hq['date']!=anchor or hq['volume']<=0 or hq['last']<=0:reason='H_missing_or_suspended_common_day'
  elif hq['timestamp'].strftime('%H:%M')<'16:00':reason='H_final_close_not_ready'
  elif anchor!=today and (anchor not in history[symbol] or history[symbol][anchor]['volume']<=0):reason='A_missing_common_day'
  row={'symbol':symbol,'name':a['name'],'H_symbol':h,'A_listing_date':x['LISTING_DATE'][:10],'factor_date':anchor,'FX_date':fxdates[-1],'HKD_CNY':rate,'excluded':reason}
  if not reason:
   ap=a['last'] if anchor==today else history[symbol][anchor]['close']
   row.update(A_close=ap,H_close_HKD=hq['last'],A_volume_shares=a['volume'],premium=ap/(hq['last']*rate)-1)
  rows.append(row)
 save(root/f'data/raw/signal_{today}.json',{'captured_at':datetime.now(TZ).isoformat(),'source':'Tencent final unadjusted quotes; daily bars only for holiday alignment','rows':rows,'historical_A_legs':history,'quotes':q,'fx':fx})
 return rows

def actions(symbols,now):
 if not symbols:return []
 codes=','.join('"'+s[2:]+'"' for s in symbols);flt='(SECURITY_CODE in ('+codes+'))'+"(EX_DIVIDEND_DATE>='"+(now.date()-timedelta(days=45)).isoformat()+"')"
 args={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','filter':flt,'pageSize':500,'pageNumber':1};j=request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(args))
 if j.get('code')==9201:return []
 if not j.get('result') or not j.get('success') or j['result']['pages']>1:raise ValueError('公司行动数据不完整')
 out=[]
 for x in j['result']['data']:
  rec=str(x.get('EQUITY_RECORD_DATE') or '')[:10];ex=str(x.get('EX_DIVIDEND_DATE') or '')[:10];notice=str(x.get('NOTICE_DATE') or '')[:10];c=x['SECURITY_CODE']
  if rec and ex and notice and notice<=now.date().isoformat():out.append({'symbol':('sh' if c.startswith('6') else 'sz')+c,'record':rec,'ex':ex,'cash':float(x.get('PRETAX_BONUS_RMB') or 0)/10,'bonus':float(x.get('BONUS_IT_RATIO') or 0)/10})
 return out
