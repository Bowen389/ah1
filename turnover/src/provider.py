"""Public quotes; full responses cached by the runner. Never manufacture a bar."""
import json,re,time,urllib.request,urllib.parse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,timedelta

def request(url,attempts=2):
 err=None
 for _ in range(attempts):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.qq.com/'})
   with urllib.request.urlopen(req,timeout=18) as r:return r.read()
  except Exception as e:err=e
 raise RuntimeError(str(err))

def parse_tencent(text):
 out={}
 for s,body in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)";',text):
  a=body.split('~')
  if len(a)<49 or not a[30] or len(a[30])<8:continue
  try:
   stamp=a[30];day=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8]
   out[s]={'name':a[1],'date':day,'timestamp':stamp,'close':a[3],'prev_close':a[4],'open':a[5],'volume':int(float(a[6])*100),'high':a[33],'low':a[34],'turnover':a[38],'upper':a[47],'lower':a[48],'source':'Tencent qt.gtimg.cn','amount_yuan':str(float(a[37])*10000)}
  except (ValueError,IndexError):continue
 return out

def quotes(symbols):
 symbols=sorted(set(symbols));groups=[symbols[i:i+70] for i in range(0,len(symbols),70)];out={};failures=[]
 def batch(ss):return parse_tencent(request('https://qt.gtimg.cn/q='+','.join(ss)).decode('gb18030',errors='replace'))
 with ThreadPoolExecutor(max_workers=8) as pool:
  jobs={pool.submit(batch,ss):ss for ss in groups}
  for f in as_completed(jobs):
   try:out.update(f.result())
   except Exception as e:failures.append({'symbols':jobs[f],'error':str(e)})
 return out,failures

def actions(day):
 d=date.fromisoformat(day);lower=(d-timedelta(days=10)).isoformat();upper=(d+timedelta(days=40)).isoformat();rows=[]
 for page in range(1,20):
  params={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','filter':"(EX_DIVIDEND_DATE>='%s')(EX_DIVIDEND_DATE<='%s')"%(lower,upper),'pageNumber':page,'pageSize':500,'sortColumns':'EX_DIVIDEND_DATE','sortTypes':1,'source':'WEB','client':'WEB'}
  raw=json.loads(request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(params)))
  if not raw.get('success') and not raw.get('result'):raise RuntimeError('公司行动接口没有返回可验证结果')
  result=raw.get('result')
  if not result:
   if raw.get('success') is True:return []
   raise RuntimeError('公司行动结果为空且状态未知')
  rows+=result.get('data',[])
  if page>=int(result.get('pages',1)):break
 else:raise RuntimeError('公司行动分页超过限制，不允许截断')
 out={}
 for a in rows:
  code=a.get('SECURITY_CODE','');s=('sh' if code.startswith('6') else 'sz')+code;ex=str(a.get('EX_DIVIDEND_DATE') or '')[:10];rec=str(a.get('EQUITY_RECORD_DATE') or '')[:10]
  if not ex or not rec:continue
  key=s+':'+ex+':'+rec
  out[key]={'id':key,'symbol':s,'ex_date':ex,'record_date':rec,'cash_per_share':str(float(a.get('PRETAX_BONUS_RMB') or 0)/10),'bonus_per_share':str(float(a.get('BONUS_IT_RATIO') or 0)/10),'source':'Eastmoney RPT_SHAREBONUS_DET','actual_payment_date_unknown':True}
 return sorted(out.values(),key=lambda x:x['id'])
