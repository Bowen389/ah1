"""GitHub Actions paper runner. Never calls a broker. China timezone throughout."""
import sys,json,csv,io,traceback,gzip
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from src import model,provider
ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo('Asia/Shanghai')
def actual(path):
 return path if path.exists() else Path(str(path)+'.gz')
def exists(path):return actual(path).exists()
def read(path):
 path=actual(path);return json.loads(gzip.decompress(path.read_bytes()).decode() if path.suffix=='.gz' else path.read_text())
def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.parent.name in ['quotes','history'] and path.suffix!='.gz':path=Path(str(path)+'.gz')
 tmp=path.with_suffix(path.suffix+'.tmp');text=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
 if path.suffix=='.gz':tmp.write_bytes(gzip.compress(text.encode(),mtime=0))
 else:tmp.write_text(text)
 tmp.replace(path)
def csv_write(path,rows):
 if not rows:
  headers={'orders.csv':['id','date','execution_time','recorded_at','side','symbol','name','qty','price','value','commission','stamp','transfer','mode'],'blocked.csv':['date','symbol','side','reason'],'equity.csv':['date','cash','market_value','dividend_receivable','nav','return_pct','positions']}
  path.write_text('\ufeff'+','.join(headers.get(path.name,['date']))+'\n');return
 fields=list(dict.fromkeys(k for row in rows for k in row));buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=fields);w.writeheader();w.writerows(rows);path.write_text('\ufeff'+buf.getvalue())
def load_history():
 seed=read(ROOT/'data/history/seed_2026-10-27.json');history={d:{} for d in seed['dates']}
 for s,values in seed['turnover'].items():
  for d,v in zip(seed['dates'],values):
   if v is not None:history[d][s]=v
 for p in sorted((ROOT/'data/history').glob('20??-??-??.json*')):
  x=read(p);history[x['date']]=x['turnover']
 return history,seed['first_observed'],seed['missing']
def signal_for(day,cal,cfg):
 p=ROOT/'data/signals'/(cal.previous(day)+'.json');expected=day==cfg['bootstrap_buy_date'] or cal.monthend(cal.previous(day))
 if not p.exists():
  if expected:raise RuntimeError('应买入日缺少前日冻结信号：'+str(p))
  return None
 sig=read(p)
 if sig['buy_date']!=day:return None
 expected_hash=sig.get('frozen_hash');body={k:v for k,v in sig.items() if k!='frozen_hash'}
 if expected_hash!=model.digest(body):raise RuntimeError('冻结信号哈希校验失败，禁止买入')
 return sig

def quote_snapshot(day,phase,symbols,now):
 p=ROOT/'data/quotes'/(day+'_'+phase+'.json')
 if exists(p):return read(p)
 qs,errors=provider.quotes(set(symbols)|{'sh000300'});index=qs.get('sh000300')
 if not index or index['date']!=day:raise RuntimeError('指数行情不是当日，拒绝旧行情或错误交易日')
 payload={'date':day,'phase':phase,'fetched_at':now.isoformat(),'quotes':qs,'errors':errors,'source':'Tencent quoted fields; raw timestamp retained'}
 # Freeze only when all symbols needed for order simulation have been requested.
 write(p,payload);return payload

def render(st,cfg,sig,status):
 lines=['# 换手率月度策略 · 20万元模拟账本','', '**模式：PAPER，纯模拟，无券商真实委托接口。**','',
 '| 项目 | 设置 |','|---|---|','| 股票 | 沪深主板，当前ST/*ST排除，上市至少365天 |','| 选股 | 过去20个交易日平均换手率最低10只 |','| 资金 | 200,000元起；最多10只；等权整手 |','| 正常周期 | 月末收盘筛选，次月首日按开盘价模拟买入，月末按收盘价模拟卖出 |','| 首期过渡 | 2026-10-28筛选，10-29买入，10-30月末卖出；下一期11-02买入 |','| 费用 | 佣金双向万一，每笔最低5元；卖出印花税万一（用户假设）；过户费双向十万分之一 |','',
 '## 当前状态','',f"- 最近执行：{status['updated_at']}；{status['phase']}；{status['status']}",f"- 可用现金：{st['cash']}元；持仓{len(st['positions'])}只；累计模拟成交{len(st['orders'])}笔。"]
 if st['equity']:
  e=st['equity'][-1];lines.append(f"- 最近收盘净值：{e['date']}，总资产{e['nav']}元，累计收益{Decimal(e['return_pct']):.2f}%。")
 if status.get('warning'):lines.append('- 数据提示：'+status['warning'])
 if sig:
  lines+=['','## 最新冻结候选','',f"信号日：{sig['signal_date']}；计划买入日：{sig['buy_date']}。候选不是保证成交。",'','| 代码 | 名称 | 20日平均换手率(%) |','|---|---|---|']
  for t in sig['targets']:lines.append(f"| {t['symbol']} | {t['name']} | {Decimal(t['mean_turnover_pct']):.4f} |")
  lines.append(f"\n有效候选池：{sig['eligible_count']}只；冻结哈希：`{sig['frozen_hash']}`。")
 lines+=['','## 明细与运行','',
 '- [逐笔模拟成交](data/orders.csv) · [每日权益](data/equity.csv) · [账户状态](data/state.json) · [运行状态](data/status.json)',
 '- [冻结信号](data/signals) · [带日期行情](data/quotes) · [拒单原因](data/blocked.csv) · [配置](config.json)',
 '- 自动时间（北京时间）：09:35/09:50记录开盘模拟，15:40/16:10完成收盘与滚动数据；非交易日退出。GitHub定时可能延迟，程序保留真实记录时间。',
 '- Actions支持手动Run workflow。重复运行幂等；缺少前一交易日账本会停止，不跳过缺失日或偷偷补记成交。',
 '- 本地：`python -m unittest discover -s tests -v`，然后 `python run.py`。仅Python 3.11+标准库，无需券商密钥。',
 '', '## 结果与数据边界','',
 '此前38.92%收益、9.37%最大回撤、14.78%后一年收益属于2024-10-28至2026-10-27的受限历史研究，不是本模拟收益，不保证重复。样本内优化未改善后一年表现，因此部署20日/10只。',
 '', '历史预热来自东方财富；前向日换手率、开收盘与真实涨跌停价采用带日期腾讯行情，股票当前名称用于ST过滤。预热有3只数据缺失，上市日期是最早可见行情的保守证明。日内停牌、数据过期及不足窗口会隔离并披露，不填假行情。当前固定初始主板列表3194只；未来新代码及代码变更需更新universe和预热。',
 '', '这是一套按真实开/收盘价记账的模拟成交模型，可能在稍后获取行情后记录，时间戳已区分成交假设时间与实际记录时间。没有验证竞价排队和实际成交量。开盘涨停拒买、收盘跌停拒卖、T+1、昨日成交量1%买入上限；不能成交不补选、不追单，延迟卖出的仓位占用名额。同名股票月底仍卖出再买。',
 '', '分红送转使用公开公司行动：登记日持仓确认权益、除权日入账，现金红利统一保守扣20%股息税，到账和送转股解锁暂按20个交易日延迟。未知除权事件会阻断持仓处理，不能用它伪造盈利。实际到账/上市日和差别股息税尚未精确复现。',
 '', '交易日历已核对上交所2026年安排，覆盖至2026-12-31；覆盖不足时停止，需补充交易所公布的新年度日历，不会把节假日按工作日猜测。',
 '', '## 调度与恢复','',
 '状态由GitHub Actions提交回main，工作流并发串行化且保留运行日志。每次修改state前核对逐笔现金，冻结信号哈希校验，模拟成交使用日期+方向+股票唯一键。若行情接口失败，工作流会失败并保留旧账本；备用时点会再试。GitHub仓库连续60天无活动可能暂停schedule，应关注Actions状态。没有配置邮件或Server酱通知，也没有向外发消息。',
 '', '来源：[腾讯行情接口](https://qt.gtimg.cn/)；[上交所2026年休市安排](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)；[GitHub定时工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。','']
 (ROOT/'README.md').write_text('\n'.join(lines))

def main():
 now=datetime.now(TZ);day=now.date().isoformat();cfg=read(ROOT/'config.json');cal= model.Calendar(read(ROOT/'data/calendar.json')['days'])
 if cfg['mode']!='PAPER':raise RuntimeError('此项目只允许PAPER')
 if day>read(ROOT/'data/calendar.json')['valid_through']:raise RuntimeError('交易日历过期')
 if not cal.session(day):print('NON_TRADING_DAY',day);return
 if now.strftime('%H:%M')<'09:30':print('WAIT_UNTIL_OPEN');return
 closing=now.strftime('%H:%M')>='15:20';phase='close' if closing else 'open';universe=read(ROOT/'data/universe.json');statepath=ROOT/'data/state.json';st=read(statepath) if statepath.exists() else model.new_state(cfg)
 sig=signal_for(day,cal,cfg) if day>=cfg['bootstrap_buy_date'] else None
 syms=list(universe) if closing else list(st['positions'])+([t['symbol'] for t in sig['targets']] if sig else [])
 snap=quote_snapshot(day,phase,syms,now);qs=snap['quotes'];priorpath=ROOT/'data/quotes'/(cal.previous(day)+'_close.json');previous=read(priorpath)['quotes'] if exists(priorpath) else {}
 if day+':open' not in st['phase_keys']:
  # An unexplained ex-right reference change cannot be silently treated as a price loss.
  for s,p in st['positions'].items():
   q=qs.get(s)
   if q and q['date']==day and abs(Decimal(q['prev_close'])-Decimal(p['last_price']))>Decimal('.02'):
    if not any(e['symbol']==s and e['ex_date']==day for e in st['entitlements']):raise RuntimeError('持仓出现未知除权/行情不连续：'+s)
  model.run_open(st,day,sig,qs,previous,cal,cfg,now.isoformat())
 warning='';latest=None
 if closing:
  history,first,missing=load_history();expected=[s for s,u in universe.items() if model.eligible_name(qs.get(s,u)['name']) and first.get(s)]
  fresh=[s for s in expected if qs.get(s,{}).get('date')==day];coverage=len(fresh)/len(expected) if expected else 0
  if coverage<cfg['minimum_quote_coverage']:
   # Incomplete snapshots are not frozen forever; next scheduled attempt retries.
   actual(ROOT/'data/quotes'/(day+'_close.json')).unlink(missing_ok=True)
   raise RuntimeError(f'收盘行情覆盖不足 {len(fresh)}/{len(expected)}；停止筛选')
  invalid=[s for s in expected if s not in set(fresh)]
  warning=f'初始预热缺失{len(missing)}只；当日未更新/隔离{len(invalid)}只；有效报价覆盖{coverage:.2%}。'
  for s,q in qs.items():
   if s in universe:universe[s]['name']=q['name']
  write(ROOT/'data/universe.json',universe)
  hist={s:q['turnover'] for s,q in qs.items() if s in universe and q['date']==day and q.get('turnover')}
  history[day]=hist;write(ROOT/'data/history'/(day+'.json'),{'date':day,'turnover':hist,'source':'Tencent day-end turnover_pct','quote_digest':model.digest(qs),'coverage':coverage,'quarantined':invalid})
  apath=ROOT/'data/actions.json';cached=read(apath)
  try:events=provider.actions(day);write(apath,{'fetched_on':day,'events':events})
  except Exception:
   if cached.get('fetched_on')!=day:raise
   events=cached['events'];warning+=' 公司行动使用当日已获取缓存。'
  model.run_close(st,day,qs,events,cal,cfg,now.isoformat())
  if day==cfg['bootstrap_signal_date'] or cal.monthend(day):
   path=ROOT/'data/signals'/(day+'.json')
   if path.exists():latest=read(path)
   else:
    latest=model.select(day,history,universe,first,cal,cfg);latest.update({'created_at':now.isoformat(),'quote_digest':model.digest(qs),'coverage':coverage,'warning':warning});latest['frozen_hash']=model.digest(latest);write(path,latest)
 model.assert_account(st,cfg);write(statepath,st)
 status={'updated_at':now.isoformat(),'phase':phase,'status':'OK','warning':warning,'mode':'PAPER','broker_orders_sent':0};write(ROOT/'data/status.json',status)
 csv_write(ROOT/'data/orders.csv',st['orders']);csv_write(ROOT/'data/equity.csv',st['equity']);csv_write(ROOT/'data/blocked.csv',st['blocked'])
 if latest is None:
  paths=sorted((ROOT/'data/signals').glob('*.json'));latest=read(paths[-1]) if paths else None
 render(st,cfg,latest,status);print(json.dumps({'status':status,'cash':st['cash'],'positions':len(st['positions']),'targets':latest['targets'] if latest else []},ensure_ascii=False,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as exc:
  write(ROOT/'data/status.json',{'status':'ERROR','updated_at':datetime.now(TZ).isoformat(),'error':str(exc),'mode':'PAPER','broker_orders_sent':0});traceback.print_exc();raise
