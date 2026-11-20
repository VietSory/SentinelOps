import * as React from 'react';
import {LogIn,LogOut} from 'lucide-react';
import App from '@/App';
import {dataset,explanationKey,incidentTime,mapExplanation,traceRecord,type ExplanationLoad} from './adapters';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useLocale } from '@/i18n/LocaleContext';
import { cn, formatVietnamStamp } from '@/lib/utils';

// The server serves the existing, tested PKCE and revision-aware API clients.
// Tokens stay in their private instances; React receives only incident state.
const clientPath = '/ui/assets/client.js';
const workspacePath = '/ui/assets/workspace.js';
type Row = Record<string, any>;
type MemoryReview = (row: Row) => Row | null;
type ExplanationSession = {controller:AbortController; seen:Set<string>; requested:Set<string>; hasFeed:boolean};
const initial = {session:false, rows:[], filters:{state:'',service:''}};
const stamp = formatVietnamStamp;
const text = (v: any) => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
function Facts({items}: {items: [string, any][]}) {
  return <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{items.map(([k,v]) => <div key={k} className="min-w-0"><dt className="text-xs text-slate-500 mb-1">{k}</dt><dd className="text-sm font-medium break-words [overflow-wrap:anywhere]">{text(v)}</dd></div>)}</dl>;
}
function Raw({value}: {value:any}) {return <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs">{JSON.stringify(value,null,2)}</pre>;}
function Notice({children,error=false}: {children:React.ReactNode,error?:boolean}) {return <div role={error?'alert':'status'} className={cn('rounded-xl border p-4 text-sm',error?'border-red-200 bg-red-50 text-red-800':'border-orange-200 bg-orange-50 text-orange-900')}>{children}</div>;}

function Proposal({row,state,engine,now,isEn,memoryReview}: {row:Row,state:Row,engine:any,now:number,isEn:boolean,memoryReview:MemoryReview}) {
  const [reason,setReason] = React.useState('');
  const p=row.proposal, images=row.review_images;
  const memory=memoryReview(row), isMemory=p.executor_runbook==='pod-memory-limit-demo/v1';
  const remaining=Math.max(0,Math.ceil((Date.parse(p.expires_at)-now)/1000));
  const current = row.current_revision === p.incident_revision;
  const pending=row.status==='pending' && remaining>0 && current && now>=Date.parse(p.created_at);
  const canDecide=pending && state.canApprove && !state.decisionLoading;
  const complete=isMemory?Boolean(memory):Boolean(images?.current_image && images?.rollback_image);
  const decision=async (choice:string) => {
    if (!reason.trim() || !canDecide || choice==='approve'&&!complete) return;
    const question = `${isEn?'Confirm':'Xác nhận'} ${choice}: ${p.runbook_id}\n${p.incident_id} / r${p.incident_revision}\n${reason.trim()}`;
    if (!window.confirm(question)) return;
    await engine.decideProposal(p.proposal_id,choice,reason.trim());
  };
  const items: [string, any][]=[[isEn?'Incident / revision':'Sự cố / revision',`${p.incident_id} / r${p.incident_revision}`]];
  if(isMemory)items.push(
    [isEn?'Kubernetes failure':'Nguyên nhân Kubernetes',memory?.cause||(isEn?'Incomplete evidence':'Bằng chứng chưa đầy đủ')],
    [isEn?'Failed pod UID':'Pod gặp lỗi (UID)',memory?.pod_uid],
    [isEn?'Memory limit':'Giới hạn RAM',memory?`${memory.memory_before} → ${memory.memory_after}`:'—'],
    ['Deployment',memory?.deployment],[isEn?'Image unchanged':'Image giữ nguyên',memory?.image],
    [isEn?'Recovery checks':'Điều kiện hồi phục',isEn?'Replacement ready, zero restarts, HTTP healthy for 30 seconds':'Pod mới Ready, không restart, HTTP ổn định 30 giây']);
  else items.push([isEn?'Match score / margin':'Điểm khớp / margin',`${p.score} / ${p.margin}`],
    [isEn?'Current image':'Image hiện tại',images?.current_image],[isEn?'Rollback image':'Image rollback',images?.rollback_image]);
  items.push([isEn?'Approval deadline':'Hạn duyệt',stamp(p.expires_at,isEn?'en':'vi')],['SHA-256',row.proposal_sha256]);
  return <Card className="p-5 space-y-4" accent={pending?'orange':'none'} data-testid="live-proposal">
    <div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold break-all">{p.runbook_id}</h3><Badge tone={row.status==='recovered'?'success':pending?'high':'neutral'}>{row.status}</Badge></div>
    <Facts items={items}/>
    {pending&&<p className="text-sm font-semibold text-orange-700">{isEn?'Time remaining':'Thời gian còn lại'}: {Math.floor(remaining/60)}:{String(remaining%60).padStart(2,'0')}</p>}
    {!current&&row.status==='pending'&&<Notice>{isEn?'Incident revision advanced. Reload the current incident.':'Incident đã có revision mới. Hãy tải lại incident hiện tại.'}</Notice>}
    {!state.canApprove&&<Notice>{isEn?'Read-only session: remediation.approve permission is required.':'Phiên chỉ đọc: cần quyền remediation.approve để quyết định.'}</Notice>}
    {row.approval&&<Raw value={row.approval}/>}
    {row.memory_action&&<Notice error={['blocked','escalated'].includes(row.memory_action.phase)}>{isEn?'Recovery: ':'Hồi phục: '}{row.memory_action.phase} · {row.memory_action.reason}</Notice>}
    {row.outcome&&!row.memory_action&&<Notice error>{row.outcome.phase}: {row.outcome.reason}</Notice>}
    <p className="text-xs text-slate-500">{isMemory?
      (isEn?'Based on the OOMKilled observed by Kubernetes. The controller rechecks the pod, image and memory limit before execution.':'Dựa trên OOMKilled do Kubernetes ghi nhận. Controller kiểm tra lại đúng pod, image và giới hạn RAM trước khi thực thi.'):
      (isEn?'Approval records a human decision. The controller still checks evidence freshness, target, quarantine and recovery telemetry.':'Duyệt ghi nhận quyết định của người trực. Controller tiếp tục kiểm tra tuổi bằng chứng, target, quarantine và telemetry hồi phục.')}</p>
    <form onSubmit={e=>e.preventDefault()} className="space-y-3"><label className="block text-sm font-medium">{isEn?'Decision reason':'Lý do quyết định'}<textarea aria-label={isEn?'Decision reason':'Lý do quyết định'} value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000} rows={2} className="block w-full mt-2 rounded-lg border border-slate-300 p-3"/></label><div className="flex flex-wrap gap-3"><Button disabled={!canDecide||!complete||!reason.trim()} onClick={()=>void decision('approve')}>{isEn?'Approve':'Duyệt'}</Button><Button variant="secondary" disabled={!canDecide||!reason.trim()} onClick={()=>void decision('reject')}>{isEn?'Reject':'Từ chối'}</Button></div></form>
  </Card>;
}

export default function LiveApp() {
  const {locale,setLocale,isEn}=useLocale();
  const [state,setState]=React.useState<Row>(initial), [mode,setMode]=React.useState('loading');
  const [error,setError]=React.useState('');
  const [contexts,setContexts]=React.useState<Record<string,Row>>({});
  const [explanations,setExplanations]=React.useState<Record<string,ExplanationLoad>>({});
  const explanationSession=React.useRef<ExplanationSession|null>(null);
  const [overview,setOverview]=React.useState<Row|null>(null),[telemetryError,setTelemetryError]=React.useState('');
  const [traceData,setTraceData]=React.useState<Record<string,Row>>({}),[traceError,setTraceError]=React.useState('');
  const traceRun=React.useRef(0);
  const [allProposals,setAllProposals]=React.useState<Row|null>(null), [watchError,setWatchError]=React.useState('');
  const [now,setNow]=React.useState(Date.now());
  const engine=React.useRef<any>(null), login=React.useRef<any>(null), client=React.useRef<any>(null);
  const validProposal=React.useRef<(row:Row)=>boolean>(()=>false);
  const memoryReview=React.useRef<MemoryReview>(()=>null);
  const tokenInput=React.useRef<HTMLInputElement>(null);
  const tr=(vi:string,en:string)=>isEn?en:vi;
  const disconnect=React.useCallback(()=>{client.current=null; login.current?.clear();engine.current?.disconnect();setAllProposals(null);},[]);
  React.useEffect(()=>{
    let disposed=false;
    const callback=window.location.pathname==='/ui/callback'?window.location.href:null;
    if(callback) window.history.replaceState(null,'','/ui/');
    async function boot(){
      try{
        const [api,workspace]=await Promise.all([import(/* @vite-ignore */ clientPath),import(/* @vite-ignore */ workspacePath)]);
        if(disposed)return;
        validProposal.current=api.validProposal;
        memoryReview.current=typeof api.memoryReview==='function'?api.memoryReview:()=>null;
        engine.current=new workspace.Workspace((s:Row)=>{if(!disposed)setState({...s});},(token:string)=>{client.current=new api.IncidentClient(token);return client.current;});
        const response=await fetch('/ui/auth-config',{cache:'no-store',credentials:'omit'});
        if(!response.ok)throw new Error('Sign-in configuration unavailable');
        const config=await response.json();if(disposed)return;
        setMode(config.mode);
        if(config.mode==='oidc'){
          login.current=new api.CognitoLogin(config);
          if(callback){
            const session=await login.current.complete(callback);if(disposed)return;
            await engine.current.connect(session.accessToken,{canApprove:session.canApprove});
            login.current.expireAt(session.expiresAt,disconnect);
          }
        }else if(callback)throw new Error('Invalid sign-in callback');
      }catch(e){if(!disposed)setError(e instanceof Error?e.message:'Sign-in unavailable');}
    }
    // A redirect to Cognito must retain the one-use PKCE transaction. Only the
    // explicit Disconnect/expiry path removes it; pagehide clears private data.
    const hide=()=>{client.current=null;login.current?.suspend();engine.current?.disconnect();setAllProposals(null);};
    void boot();window.addEventListener('pagehide',hide);
    return()=>{disposed=true;window.removeEventListener('pagehide',hide);disconnect();};
  },[disconnect]);
  React.useEffect(()=>{const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  React.useEffect(()=>{
    if(!state.session){client.current=null;setAllProposals(null);setWatchError('');return;}
    let cancelled=false;const controller=new AbortController();
    const read=async()=>{try{const body=await client.current.get('/v1/runbook-proposals?limit=100',controller.signal);if(!Array.isArray(body.items)||typeof body.has_more!=='boolean'||body.items.some((row:Row)=>!validProposal.current(row)))throw new Error('Invalid proposal feed');if(!cancelled){setAllProposals(body);setWatchError('');}}catch(e:any){if(cancelled)return;if(e.status===401||e.status===403)disconnect();else setWatchError(e.message);}};
    void read();const poll=setInterval(()=>void read(),10000), listPoll=setInterval(()=>void engine.current?.loadList(),30000);
    return()=>{cancelled=true;controller.abort();clearInterval(poll);clearInterval(listPoll);};
  },[state.session,disconnect]);

  React.useEffect(()=>{
    if (!state.session) {
      setExplanations({});
      return;
    }
    const session={controller:new AbortController(),seen:new Set<string>(),requested:new Set<string>(),hasFeed:false};
    explanationSession.current=session;
    return ()=>{
      session.controller.abort();
      explanationSession.current=null;
    };
  },[state.session]);

  const loadExplanation=React.useCallback(async(id:string,revision:number,detail?:Row)=>{
    const activeClient=client.current;
    const session=explanationSession.current;
    const key=explanationKey(id,revision);
    const isRecovered=()=>engine.current?.state.rows.find((row:Row)=>row.incident_id===id)?.state==='recovered';
    if (!activeClient || !session || session.requested.has(key) || isRecovered() || detail?.payload.state==='recovered') return;

    const signal=session.controller.signal;
    const current=()=>!signal.aborted && activeClient===client.current && engine.current?.state.session;
    session.requested.add(key);
    setExplanations(previous=>({...previous,[key]:{aiExplanationStatus:'loading'}}));
    try {
      const context=detail??await activeClient.context(id,revision,0,signal);
      if (!current()) return;
      if (context.payload.state==='recovered' || isRecovered()) {
        session.requested.delete(key);
        setExplanations(previous=>{
          const next={...previous};
          delete next[key];
          return next;
        });
        return;
      }
      const evidenceIds=context.payload.evidence.map((item:Row)=>item.id);
      const response=await activeClient.explanation(id,revision,evidenceIds,signal);
      if (!current()) return;
      const aiExplanation=mapExplanation(response);
      setExplanations(previous=>({...previous,[key]:{aiExplanationStatus:'ready',aiExplanation}}));
    } catch (failure) {
      if (!current()) return;
      const error=failure as Error & {status?:number;code?:string};
      if (error.status===401 || (error.status===403 && error.code==='insufficient_scope')) {
        engine.current.disconnect(error.message);
        return;
      }
      setExplanations(previous=>({...previous,[key]:{aiExplanationStatus:'error',aiExplanationError:error.message}}));
    }
  },[]);

  const detail=state.detail, selectedId=detail?.incident_id, revision=detail?.revision;
  React.useEffect(()=>{
    if (!selectedId || !revision) return;
    void engine.current.loadProposals();
    void engine.current.loadActions();
    void loadExplanation(selectedId,revision,engine.current.state.detail);
  },[selectedId,revision,loadExplanation]);

  React.useEffect(()=>{
    const session=explanationSession.current;
    if (!session || state.listLoading || state.listError || !state.updatedAt) return;
    const rows:Row[]=state.rows;
    const unseen=rows.filter(row=>!session.seen.has(row.incident_id));
    rows.forEach(row=>session.seen.add(row.incident_id));
    if (!session.hasFeed) {
      session.hasFeed=true;
      return;
    }
    const newest=unseen.filter(row=>row.state!=='recovered').sort((left,right)=>
      incidentTime(right.detected_at,right.started_at)-incidentTime(left.detected_at,left.started_at)||
      left.incident_id.localeCompare(right.incident_id))[0];
    if (newest) void loadExplanation(newest.incident_id,newest.revision);
  },[state.rows,state.listLoading,state.listError,state.updatedAt,loadExplanation]);

  React.useEffect(()=>{
    if(!state.session){setContexts({});setOverview(null);setTelemetryError('');setTraceData({});setTraceError('');traceRun.current++;return;}
    let cancelled=false;const abort=new AbortController();const activeClient=client.current;
    const read=async()=>{
      try{const value=await activeClient.get('/v1/observability/overview',abort.signal,{timeoutMs:30000});if(value.schema_version!=='live-observability/v1'||!Array.isArray(value.services))throw new Error('Invalid telemetry response');if(!cancelled){setOverview(value);setTelemetryError(value.unavailable?.length?'Some telemetry sources are unavailable':'');}}
      catch(e:any){if(cancelled)return;if(e.status===401||e.status===403)disconnect();else{setOverview(null);setTelemetryError(e.message);}}
    };
    void read();const timer=setInterval(()=>void read(),30000);
    return()=>{cancelled=true;abort.abort();clearInterval(timer);};
  },[state.session,disconnect]);
  const loadTraces=React.useCallback(async(recent=false)=>{
    const value=engine.current?.state.detail,active=client.current;if(!value||!active)return;
    const run=++traceRun.current,id=value.incident_id,rev=value.revision;
    const current=()=>run===traceRun.current&&active===client.current&&engine.current?.state.detail?.incident_id===id&&engine.current?.state.detail?.revision===rev;
    try{
      const ids=[...new Set<string>((value.payload.evidence||[]).filter((e:Row)=>e.type==='trace').flatMap((e:Row)=>/^Trace ([0-9a-f]{32}): /.exec(e.fact)?.[1]||[]))].slice(0,3);
      let result:Row={items:[],association:'recorded'};
      if(!recent&&ids.length){
        const replies=await Promise.allSettled(ids.map(trace=>active.get('/v1/observability/traces/'+trace)));
        result.items=replies.flatMap(r=>r.status==='fulfilled'?r.value.items:[]);
      }
      if(!result.items.length){
        const query=new URLSearchParams({service:'checkout-service'});
        if(!recent){const at=Date.parse(value.payload.detected_at);query.set('start',String(Math.max(0,(at-600000)*1000)));query.set('end',String(Math.min(Date.now(),at+1200000)*1000));}
        result=await active.get('/v1/observability/trace-search?'+query);
      }
      if(!Array.isArray(result.items))throw new Error('Invalid trace response');result.items.forEach((item:Row)=>traceRecord(item,result.association,false));
      if(current()){setTraceData(old=>({...old,[id]:result}));setTraceError(result.items.length?'':'No trace data retained for this time window');}
    }catch(e:any){if(current()){setTraceError(e.message);setTraceData(old=>({...old,[id]:{items:[],association:recent?'recent':'window'}}));}}
  },[]);
  React.useEffect(()=>{if(!detail)return;setContexts(old=>({...old,[detail.incident_id]:detail}));void loadTraces();return()=>{traceRun.current++;};},[selectedId,revision,loadTraces]);
  const traces=React.useMemo(()=>Object.fromEntries(Object.entries(traceData).map(([id,result])=>[id,result.items.map((item:Row)=>traceRecord(item,result.association,isEn))])),[traceData,isEn]);
  const proposals=React.useMemo(()=>[...new Map([...(allProposals?.items||[]),...(state.proposals||[])].map((row:Row)=>[row.proposal.proposal_id,row])).values()] as Row[],[allProposals,state.proposals]);
  const data=React.useMemo(()=>dataset(state.rows||[],contexts,overview,traces,proposals,state.actions?.items||[],isEn,explanations),[state.rows,contexts,overview,traces,proposals,state.actions,isEn,explanations]);
  const selectedExplanation=selectedId&&revision?explanations[explanationKey(selectedId,revision)]:undefined;
  const select=(id:string)=>{if(engine.current?.state.selected!==id||!engine.current?.state.detail)void engine.current?.select(id);};
  const gate=<Card className="p-6 sm:p-10 max-w-2xl mx-auto mt-8 space-y-5"><h2 className="text-xl font-semibold">{tr('Kết nối workspace vận hành','Connect to your operator workspace')}</h2><p className="text-sm text-slate-500">{tr('Đăng nhập để xem dashboard, trace waterfall và service map bằng dữ liệu thật.','Sign in for live dashboards, trace waterfalls and the service map.')}</p>{mode==='oidc'&&<Button onClick={async()=>{try{setLocale(locale);window.location.assign(await login.current.begin());}catch(e:any){setError(e.message);}}}><LogIn className="h-4 w-4"/>{tr('Đăng nhập Cognito','Sign in with Cognito')}</Button>}{mode==='manual'&&<form className="flex flex-wrap gap-3" onSubmit={async e=>{e.preventDefault();const input=tokenInput.current;if(!input?.value)return;const value=input.value;input.value='';await engine.current.connect(value);}}><input ref={tokenInput} type="password" autoComplete="off" aria-label="API access token" className="rounded-lg border px-3 py-2"/><Button type="submit">{tr('Kết nối','Connect')}</Button></form>}{mode==='loading'&&<p role="status">{tr('Đang kiểm tra đăng nhập…','Checking sign-in…')}</p>}{mode==='unavailable'&&<Notice error>{tr('Đăng nhập chưa được cấu hình.','Sign-in is not configured.')}</Notice>}<a href="/ui/demo/" className="text-sm text-orange-700 underline block">{tr('Mở giao diện mô phỏng gốc','Open the original simulation')}</a></Card>;
  const review=<div className="space-y-4"><div className="flex flex-wrap gap-3 justify-between"><h2 className="font-semibold">{tr('Duyệt proposal theo revision thật','Review the actual revision-bound proposal')}</h2><Button size="sm" variant="secondary" disabled={state.proposalsLoading||state.decisionLoading||!detail} onClick={()=>void engine.current.loadProposals()}>{tr('Tải lại đề xuất','Reload proposals')}</Button></div>{state.proposalsError&&<Notice error>{state.proposalsError.message} · {tr('Tải lại để xác minh kết quả trước khi quyết định tiếp.','Reload to verify the outcome before another decision.')}</Notice>}{state.proposalsLoading&&<p>{tr('Đang tải…','Loading…')}</p>}{state.proposals?.length===0&&<Notice>{tr('Revision này không có proposal để duyệt.','No proposal for this revision.')}</Notice>}{state.proposals?.map((row:Row)=><Proposal key={row.proposal.proposal_id} row={row} state={state} engine={engine.current} now={now} isEn={isEn} memoryReview={memoryReview.current}/>)}</div>;
  const journal=<Card className="p-5 space-y-3"><h3 className="font-semibold">{tr('Nhật ký thực thi / Giải thích','Execution journal / Explanation')}</h3>{state.actionsError&&<Notice error>{state.actionsError.message}</Notice>}{state.actions?.items.map((item:Row)=><details key={item.action_id}><summary className="cursor-pointer text-sm break-all">{item.phase} · {item.action_id}</summary><Raw value={item}/></details>)}{selectedExplanation?.aiExplanationStatus==='ready'&&<p className="whitespace-pre-wrap text-sm">{selectedExplanation.aiExplanation.summary}</p>}{selectedExplanation?.aiExplanationStatus==='error'&&<Notice error>{selectedExplanation.aiExplanationError}</Notice>}</Card>;
  const notices=<div className="space-y-3 mb-5">{error&&<Notice error>{error}</Notice>}{state.notice&&<Notice error>{state.notice}</Notice>}{state.listError&&<Notice error>{state.listError.message}</Notice>}{watchError&&<Notice error>{watchError}</Notice>}{telemetryError&&<Notice error>{telemetryError}</Notice>}{traceError&&state.session&&<Notice>{tr('Trace: ','Trace: ')}{traceError}</Notice>}{state.session&&<p className="text-xs text-slate-500">{tr('Telemetry từ Prometheus; incident trong feed đã tải. Cạnh trace theo span đã ghi nhận.','Telemetry from Prometheus; incidents from loaded feed. Trace edges follow recorded spans.')} {overview?.as_of&&`${tr('Đo lúc','Observed')} ${stamp(overview.as_of,locale)}`}{state.nextCursor&&<Button size="sm" variant="ghost" onClick={()=>void engine.current.loadList(true)}>{tr('Tải thêm incident','Load more incidents')}</Button>}</p>}</div>;
  return <App live={{...data,connected:state.session,selectedId:state.selected||'',onSelect:select,gate,
    header:state.session?<Button size="sm" variant="ghost" onClick={disconnect}><LogOut className="h-4 w-4"/>{tr('Ngắt kết nối','Disconnect')}</Button>:null,
    notice:notices,reviewPanel:review,journal,trend:data.trend,
    onRefresh:()=>{void engine.current?.loadList();if(detail)void engine.current.select(detail.incident_id);},onRecentTraces:()=>void loadTraces(true)}}/>;
}
