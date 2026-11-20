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
const MAX_INCIDENT_PAGES = 20;
const CONTEXT_HYDRATE_LIMIT = 60;
const CONTEXT_HYDRATE_CONCURRENCY = 6;
type Row = Record<string, any>;
type MemoryReview = (row: Row) => Row | null;
type ExplanationSession = {controller:AbortController; seen:Set<string>; requested:Set<string>; hasFeed:boolean};
const initial = {session:false, rows:[], filters:{state:'',service:''}};
const stamp = formatVietnamStamp;
const text = (v: any) => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
function mergeIncidentRows(base: Row[], extra: Row[]): Row[] {
  const byId = new Map<string, Row>();
  for (const row of [...base, ...extra]) {
    const existing = byId.get(row.incident_id);
    if (!existing || row.revision >= existing.revision) byId.set(row.incident_id, row);
  }
  return [...byId.values()];
}
function validIncidentPage(body: any): boolean {
  return Boolean(
    body &&
    Array.isArray(body.items) &&
    (body.next_cursor === null || typeof body.next_cursor === 'string') &&
    !body.items.some((row: Row) => !row?.incident_id || !Number.isSafeInteger(row.revision) || row.revision < 1),
  );
}
function Facts({items}: {items: [string, any][]}) {
  return <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{items.map(([k,v]) => <div key={k} className="min-w-0"><dt className="text-xs text-slate-500 mb-1">{k}</dt><dd className="text-sm font-medium break-words [overflow-wrap:anywhere]">{text(v)}</dd></div>)}</dl>;
}
function Raw({value}: {value:any}) {return <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs">{JSON.stringify(value,null,2)}</pre>;}
function Notice({children,error=false}: {children:React.ReactNode,error?:boolean}) {return <div role={error?'alert':'status'} className={cn('rounded-xl border p-4 text-sm',error?'border-red-200 bg-red-50 text-red-800':'border-orange-200 bg-orange-50 text-orange-900')}>{children}</div>;}

function Proposal({row,state,engine,now,isEn,memoryReview,memoryRecoveryReview,onDecided}: {row:Row,state:Row,engine:any,now:number,isEn:boolean,memoryReview:MemoryReview,memoryRecoveryReview:MemoryReview,onDecided?:()=>void}) {
  const [reason,setReason] = React.useState('');
  const p=row.proposal, images=row.review_images;
  const evidence=memoryReview(row), recovery=memoryRecoveryReview(row), memory=evidence||recovery, isMemory=p.executor_runbook==='pod-memory-limit-demo/v1';
  const remaining=Math.max(0,Math.ceil((Date.parse(p.expires_at)-now)/1000));
  const current = row.current_revision === p.incident_revision;
  const hasHash = typeof row.proposal_sha256 === 'string' && /^[a-f0-9]{64}$/.test(row.proposal_sha256);
  const pending=row.status==='pending' && remaining>0 && current && hasHash && now>=Date.parse(p.created_at);
  const canDecide=pending && state.canApprove && !state.decisionLoading;
  const canApprove=(isMemory?row.manual_recovery_available===true:pending) && state.canApprove && !state.decisionLoading;
  const complete=isMemory?Boolean(recovery):Boolean(images?.current_image && images?.rollback_image);
  const decision=async (choice:string) => {
    if (!reason.trim() || !(choice==='approve'?canApprove:canDecide) || choice==='approve'&&!complete) return;
    const question = `${isEn?'Confirm':'Xác nhận'} ${choice}: ${p.runbook_id}\n${p.incident_id} / r${p.incident_revision}\n${reason.trim()}`;
    if (!window.confirm(question)) return;
    await engine.decideProposal(p.proposal_id,choice,reason.trim());
    onDecided?.();
  };
  const items: [string, any][]=[[isEn?'Incident / revision':'Sự cố / revision',`${p.incident_id} / r${p.incident_revision}`]];
  if(isMemory)items.push(
    [isEn?'Kubernetes failure':'Nguyên nhân Kubernetes',evidence?.cause||'—'],
    [isEn?'Failed pod UID':'Pod gặp lỗi (UID)',evidence?.pod_uid],
    [isEn?'Memory limit':'Giới hạn RAM',memory?`${memory.memory_before} → ${memory.memory_after}`:'—'],
    [isEn?'Deployment / container':'Deployment / container',memory?`${memory.deployment} / ${memory.container}`:'—'],[isEn?'Image unchanged':'Image giữ nguyên',memory?.image],
    [isEn?'Recovery checks':'Điều kiện hồi phục',isEn?'Replacement ready, zero restarts, HTTP healthy for 30 seconds':'Pod mới Ready, không restart, HTTP ổn định 30 giây']);
  else items.push([isEn?'Match score / margin':'Điểm khớp / margin',`${p.score} / ${p.margin}`],
    [isEn?'Current image':'Image hiện tại',images?.current_image],[isEn?'Rollback image':'Image rollback',images?.rollback_image]);
  items.push([isEn?'Approval deadline':'Hạn duyệt',stamp(p.expires_at,isEn?'en':'vi')],['SHA-256',row.proposal_sha256]);
  return <Card className="p-5 space-y-4" accent={pending?'orange':'none'} data-testid="live-proposal">
    <div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold break-all">{p.runbook_id}</h3><Badge tone={row.status==='recovered'?'success':pending?'high':'neutral'}>{row.status}</Badge></div>
    <Facts items={items}/>
    {pending&&<p className="text-sm font-semibold text-orange-700">{isEn?'Time remaining':'Thời gian còn lại'}: {Math.floor(remaining/60)}:{String(remaining%60).padStart(2,'0')}</p>}
    {!current&&<Notice>{isEn?'Incident revision advanced. Reload the current incident.':'Incident đã có revision mới. Hãy tải lại incident hiện tại.'}</Notice>}
    {!complete&&<Notice>{isMemory?(isEn?'Complete pinned memory change evidence is required before approval.':'Cần đủ bằng chứng thay đổi bộ nhớ được ghim trong đề xuất trước khi duyệt.'):(isEn?'Both pinned image digests are required before approval.':'Cần đủ hai image digest được ghim trong đề xuất trước khi duyệt.')}</Notice>}
    {!state.canApprove&&<Notice>{isEn?'Read-only session: remediation.approve permission is required.':'Phiên chỉ đọc: cần quyền remediation.approve để quyết định.'}</Notice>}
    {row.approval&&<Raw value={row.approval}/>}
    {row.memory_action&&<Notice error={['blocked','escalated'].includes(row.memory_action.phase)}>{isEn?'Recovery: ':'Hồi phục: '}{row.memory_action.phase} · {row.memory_action.reason}</Notice>}
    {row.outcome&&!row.memory_action&&<Notice error>{row.outcome.phase}: {row.outcome.reason}</Notice>}
    <p className="text-xs text-slate-500">{isMemory?
      (isEn?'Approve fixed 64Mi → 256Mi memory recovery for the pinned target. The controller rechecks the deployment, image and memory limit before execution.':'Duyệt khôi phục RAM cố định 64Mi → 256Mi cho target đã ghim. Controller kiểm tra lại deployment, image và giới hạn RAM trước khi thực thi.'):
      (isEn?'Approval records a human decision. The controller still checks evidence freshness, target, quarantine and recovery telemetry.':'Duyệt ghi nhận quyết định của người trực. Controller tiếp tục kiểm tra tuổi bằng chứng, target, quarantine và telemetry hồi phục.')}</p>
    <form onSubmit={e=>e.preventDefault()} className="space-y-3"><label className="block text-sm font-medium">{isEn?'Decision reason':'Lý do quyết định'}<textarea aria-label={isEn?'Decision reason':'Lý do quyết định'} value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000} rows={2} className="block w-full mt-2 rounded-lg border border-slate-300 p-3"/></label><div className="flex flex-wrap gap-3"><Button disabled={!canApprove||!complete||!reason.trim()} onClick={()=>void decision('approve')}>{isEn?'Approve':'Duyệt'}</Button><Button variant="secondary" disabled={!canDecide||!reason.trim()} onClick={()=>void decision('reject')}>{isEn?'Reject':'Từ chối'}</Button></div></form>
  </Card>;
}

export default function LiveApp() {
  const {locale,setLocale,isEn}=useLocale();
  const [state,setState]=React.useState<Row>(initial), [mode,setMode]=React.useState('loading');
  const [error,setError]=React.useState('');
  const [loadedRows,setLoadedRows]=React.useState<Row[]>([]);
  const [feedReady,setFeedReady]=React.useState(false);
  const [feedUpdatedAt,setFeedUpdatedAt]=React.useState<string|null>(null);
  const [pagesLoading,setPagesLoading]=React.useState(false);
  const [pagesError,setPagesError]=React.useState('');
  const [remainingCursor,setRemainingCursor]=React.useState<string|null>(null);
  const loadedRowsRef=React.useRef<Row[]>([]);
  loadedRowsRef.current=loadedRows;
  const lastDrainedRowsRef=React.useRef<Row[]|null>(null);
  const activeDrainRef=React.useRef<AbortController|null>(null);
  const [contexts,setContexts]=React.useState<Record<string,Row>>({});
  const contextsRef=React.useRef<Record<string,Row>>({});
  contextsRef.current=contexts;
  const hydratedContextsRef=React.useRef<Set<string>>(new Set());
  const hydrationControllerRef=React.useRef<AbortController|null>(null);
  const [explanations,setExplanations]=React.useState<Record<string,ExplanationLoad>>({});
  const explanationSession=React.useRef<ExplanationSession|null>(null);
  const [overview,setOverview]=React.useState<Row|null>(null),[telemetryError,setTelemetryError]=React.useState('');
  const [traceData,setTraceData]=React.useState<Record<string,Row>>({}),[traceError,setTraceError]=React.useState('');
  const traceRun=React.useRef(0);
  const [allProposals,setAllProposals]=React.useState<Row|null>(null), [watchError,setWatchError]=React.useState('');
  const actionFeedRef=React.useRef<Record<string,Row[]>>({});
  const [proposalActions,setProposalActions]=React.useState<Row[]>([]);
  const [now,setNow]=React.useState(Date.now());
  const engine=React.useRef<any>(null), login=React.useRef<any>(null), client=React.useRef<any>(null);
  const validProposal=React.useRef<(row:Row)=>boolean>(()=>false);
  const memoryReview=React.useRef<MemoryReview>(()=>null);
  const memoryRecoveryReview=React.useRef<MemoryReview>(()=>null);
  const tokenInput=React.useRef<HTMLInputElement>(null);
  const tr=(vi:string,en:string)=>isEn?en:vi;
  const disconnect=React.useCallback(()=>{
    activeDrainRef.current?.abort();activeDrainRef.current=null;
    hydrationControllerRef.current?.abort();hydrationControllerRef.current=null;
    hydratedContextsRef.current.clear();
    lastDrainedRowsRef.current=null;
    client.current=null; login.current?.clear();engine.current?.disconnect();
    setLoadedRows([]);setFeedReady(false);setFeedUpdatedAt(null);setPagesLoading(false);setPagesError('');setRemainingCursor(null);
    setAllProposals(null);
    actionFeedRef.current={};setProposalActions([]);
  },[]);
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
        memoryRecoveryReview.current=typeof api.memoryRecoveryReview==='function'?api.memoryRecoveryReview:()=>null;
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

  const readProposals=React.useCallback(async(signal?:AbortSignal)=>{
    const activeClient=client.current;
    if(!activeClient)return;
    try{
      const body=await activeClient.get('/v1/runbook-proposals?limit=100',signal);
      if(signal?.aborted || activeClient!==client.current)return;
      if(!Array.isArray(body.items)||typeof body.has_more!=='boolean'||body.items.some((row:Row)=>!validProposal.current(row)))throw new Error('Invalid proposal feed');
      setAllProposals(body);setWatchError('');
    }catch(e:any){
      if(signal?.aborted || e?.name==='AbortError' || activeClient!==client.current)return;
      if(e.status===401||e.status===403)disconnect();else setWatchError(e.message);
    }
  },[disconnect]);

  React.useEffect(()=>{
    if(!state.session){client.current=null;setAllProposals(null);setWatchError('');return;}
    const controller=new AbortController();
    void readProposals(controller.signal);
    const poll=setInterval(()=>void readProposals(controller.signal),10000);
    const listPoll=setInterval(()=>void engine.current?.loadList(),30000);
    return()=>{controller.abort();clearInterval(poll);clearInterval(listPoll);};
  },[state.session,readProposals]);

  React.useEffect(()=>{
    const activeClient=client.current;
    if(!state.session||!activeClient||!allProposals)return;
    const controller=new AbortController();
    const pending=new Map<string,Row>();
    for(const row of allProposals.items){
      const proposal=row.proposal;
      if(proposal.executor_runbook!=='payment-rollback-known-good/v1'||!['approved','consumed','engineer'].includes(row.status)
        ||row.approval?.decision!=='approve')continue;
      const key=explanationKey(proposal.incident_id,proposal.incident_revision);
      if(!actionFeedRef.current[key]?.some(action=>['recovered','blocked','escalated'].includes(action.phase))){
        pending.set(key,proposal);
      }
    }
    const entries=[...pending.entries()];
    async function collect(){
      for(let offset=0;offset<entries.length;offset+=4){
        if(controller.signal.aborted||activeClient!==client.current)return;
        const batch=entries.slice(offset,offset+4);
        const responses=await Promise.allSettled(batch.map(([,p])=>
          activeClient.actions(p.incident_id,p.incident_revision,null,controller.signal)));
        if(controller.signal.aborted||activeClient!==client.current)return;
        responses.forEach((response,index)=>{
          if(response.status==='fulfilled'){
            const [key,proposal]=batch[index];
            actionFeedRef.current[key]=response.value.items.filter((item:Row)=>item.incident_revision===proposal.incident_revision);
          }
        });
        setProposalActions(Object.values(actionFeedRef.current).flat());
      }
    }
    void collect();
    return()=>controller.abort();
  },[allProposals,state.session]);

  // Drain all remaining pages from GET /v1/incidents (which returns rows in ascending
  // created_at order) before exposing the merged dataset so newest incidents are never missed.
  React.useEffect(()=>{
    if(!state.session){
      activeDrainRef.current?.abort();activeDrainRef.current=null;
      lastDrainedRowsRef.current=null;
      setLoadedRows([]);setFeedReady(false);setFeedUpdatedAt(null);setPagesLoading(false);setPagesError('');setRemainingCursor(null);
      return;
    }
    const wsState=engine.current?.state;
    if(!wsState || state.listLoading || state.listError || !state.updatedAt) return;
    if(wsState.rows === lastDrainedRowsRef.current) return;
    lastDrainedRowsRef.current=wsState.rows;
    const activeClient=client.current;
    if(!activeClient) return;
    const firstRows:Row[]=wsState.rows||[];
    const initialCursor:string|null=wsState.nextCursor??null;
    const updatedAt:string=state.updatedAt;
    const filters:Row=wsState.filters||{};
    activeDrainRef.current?.abort();
    if(!initialCursor){
      activeDrainRef.current=null;
      setLoadedRows(mergeIncidentRows(firstRows,[]));
      setRemainingCursor(null);
      setPagesError('');
      setPagesLoading(false);
      setFeedReady(true);
      setFeedUpdatedAt(updatedAt);
      return;
    }
    const controller=new AbortController();
    activeDrainRef.current=controller;
    setPagesLoading(true);
    setPagesError('');
    const drain=async()=>{
      const collected:Row[]=[...firstRows];
      const seenCursors=new Set<string>();
      let cursor:string|null=initialCursor;
      let pageCount=1;
      try{
        while(cursor && pageCount<MAX_INCIDENT_PAGES && !seenCursors.has(cursor)){
          seenCursors.add(cursor);
          const query: URLSearchParams=new URLSearchParams({limit:'100',cursor});
          if(filters.state) query.set('state',filters.state);
          if(filters.service) query.set('service',filters.service);
          const body: Row=await activeClient.get(`/v1/incidents?${query}`,controller.signal);
          if(controller.signal.aborted || activeClient!==client.current) return;
          if(!validIncidentPage(body)) throw new Error('Invalid incident page response');
          collected.push(...body.items);
          cursor=body.next_cursor;
          pageCount+=1;
        }
        if(controller.signal.aborted || activeClient!==client.current) return;
        setLoadedRows(mergeIncidentRows(collected,[]));
        setRemainingCursor(cursor && !seenCursors.has(cursor) ? cursor : null);
        setPagesError('');
        setFeedReady(true);
        setFeedUpdatedAt(updatedAt);
      }catch(e:any){
        if(controller.signal.aborted || e?.name==='AbortError' || activeClient!==client.current) return;
        if(e.status===401 || (e.status===403 && e.code==='insufficient_scope')){disconnect();return;}
        setLoadedRows(mergeIncidentRows(collected,[]));
        setRemainingCursor(cursor);
        setPagesError(e.message||'Could not load all incident pages');
        setFeedReady(true);
        setFeedUpdatedAt(updatedAt);
      }finally{
        if(activeDrainRef.current===controller){activeDrainRef.current=null;setPagesLoading(false);}
      }
    };
    void drain();
  },[state.session,state.listLoading,state.listError,state.updatedAt,state.rows,state.nextCursor,disconnect]);

  const loadMorePages=React.useCallback(async()=>{
    const activeClient=client.current;
    const startCursor=remainingCursor;
    if(!activeClient || !startCursor || pagesLoading) return;
    const filters:Row=engine.current?.state.filters||{};
    activeDrainRef.current?.abort();
    const controller=new AbortController();
    activeDrainRef.current=controller;
    setPagesLoading(true);
    setPagesError('');
    const collected:Row[]=[];
    const seenCursors=new Set<string>();
    let cursor:string|null=startCursor;
    let pageCount=0;
    try{
      while(cursor && pageCount<MAX_INCIDENT_PAGES && !seenCursors.has(cursor)){
        seenCursors.add(cursor);
        const query: URLSearchParams=new URLSearchParams({limit:'100',cursor});
        if(filters.state) query.set('state',filters.state);
        if(filters.service) query.set('service',filters.service);
        const body: Row=await activeClient.get(`/v1/incidents?${query}`,controller.signal);
        if(controller.signal.aborted || activeClient!==client.current) return;
        if(!validIncidentPage(body)) throw new Error('Invalid incident page response');
        collected.push(...body.items);
        cursor=body.next_cursor;
        pageCount+=1;
      }
      if(controller.signal.aborted || activeClient!==client.current) return;
      setLoadedRows(old=>mergeIncidentRows(old,collected));
      setRemainingCursor(cursor && !seenCursors.has(cursor) ? cursor : null);
      setPagesError('');
    }catch(e:any){
      if(controller.signal.aborted || e?.name==='AbortError' || activeClient!==client.current) return;
      if(e.status===401 || (e.status===403 && e.code==='insufficient_scope')){disconnect();return;}
      if(collected.length) setLoadedRows(old=>mergeIncidentRows(old,collected));
      setRemainingCursor(cursor);
      setPagesError(e.message||'Could not load additional incident pages');
    }finally{
      if(activeDrainRef.current===controller){activeDrainRef.current=null;setPagesLoading(false);}
    }
  },[remainingCursor,pagesLoading,disconnect]);

  // Hydrate incident contexts in the background so list rows display recorded root_cause_ranking
  // scores and suspected services instead of falling back to abstained/unconcluded placeholders.
  React.useEffect(()=>{
    if(!state.session){
      hydrationControllerRef.current?.abort();
      hydrationControllerRef.current=null;
      hydratedContextsRef.current.clear();
      return;
    }
    const controller=new AbortController();
    hydrationControllerRef.current=controller;
    return()=>{
      controller.abort();
      if(hydrationControllerRef.current===controller) hydrationControllerRef.current=null;
      hydratedContextsRef.current.clear();
    };
  },[state.session]);

  React.useEffect(()=>{
    const activeClient=client.current;
    const controller=hydrationControllerRef.current;
    if(!state.session || !activeClient || !controller || !loadedRows.length) return;
    const sorted=[...loadedRows].sort((left,right)=>
      incidentTime(right.detected_at,right.started_at)-incidentTime(left.detected_at,left.started_at)||
      left.incident_id.localeCompare(right.incident_id));
    const pending=sorted.filter((row,index)=>{
      if(index>=CONTEXT_HYDRATE_LIMIT && row.state==='recovered') return false;
      if(Array.isArray(row.root_cause_ranking)) return false;
      if(contextsRef.current[row.incident_id]?.revision>=row.revision) return false;
      const key=explanationKey(row.incident_id,row.revision);
      return !hydratedContextsRef.current.has(key);
    });
    if(!pending.length) return;
    pending.forEach(row=>hydratedContextsRef.current.add(explanationKey(row.incident_id,row.revision)));
    const signal=controller.signal;
    let cursorIndex=0;
    const worker=async()=>{
      while(!signal.aborted && activeClient===client.current){
        const row=pending[cursorIndex++];
        if(!row) return;
        const key=explanationKey(row.incident_id,row.revision);
        try{
          const envelope=await activeClient.context(row.incident_id,row.revision,0,signal);
          if(signal.aborted || activeClient!==client.current) return;
          setContexts(old=>old[row.incident_id]?.revision>=envelope.revision?old:{...old,[row.incident_id]:envelope});
        }catch(e:any){
          if(signal.aborted || e?.name==='AbortError'){hydratedContextsRef.current.delete(key);return;}
          if(e.status===401 || (e.status===403 && e.code==='insufficient_scope')){disconnect();return;}
        }
      }
    };
    const count=Math.min(CONTEXT_HYDRATE_CONCURRENCY,pending.length);
    for(let i=0;i<count;i++) void worker();
  },[state.session,loadedRows,disconnect]);

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
    const isRecovered=()=>loadedRowsRef.current.find((row:Row)=>row.incident_id===id)?.state==='recovered';
    if (!activeClient || !session || session.requested.has(key) || isRecovered() || detail?.payload.state==='recovered') return;

    const signal=session.controller.signal;
    const current=()=>!signal.aborted && activeClient===client.current && engine.current?.state.session;
    session.requested.add(key);
    setExplanations(previous=>({...previous,[key]:{aiExplanationStatus:'loading'}}));
    try {
      const cached=contextsRef.current[id];
      const context=detail??(cached?.revision===revision?cached:await activeClient.context(id,revision,0,signal));
      if (!current()) return;
      setContexts(old=>old[id]?.revision>=context.revision?old:{...old,[id]:context});
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
    if (!state.session || !selectedId || !revision || state.detailLoading || state.decisionLoading) return;
    const latest=loadedRows.find((row:Row)=>row.incident_id===selectedId);
    if (latest && latest.revision>revision) void engine.current?.select(selectedId);
  },[state.session,selectedId,revision,state.detailLoading,state.decisionLoading,loadedRows]);

  React.useEffect(()=>{
    if (!state.session || !selectedId || !revision || state.proposals===null || state.proposalsLoading || state.decisionLoading || state.proposalsError) return;
    const known=new Set((state.proposals||[]).map((row:Row)=>row.proposal.proposal_id));
    const hasMissing=(allProposals?.items||[]).some((row:Row)=>
      row.proposal.incident_id===selectedId && row.proposal.incident_revision===revision && !known.has(row.proposal.proposal_id));
    if (hasMissing) void engine.current?.loadProposals();
  },[state.session,selectedId,revision,state.proposals,state.proposalsLoading,state.decisionLoading,state.proposalsError,allProposals]);

  React.useEffect(()=>{
    const session=explanationSession.current;
    if (!session || !feedReady || pagesLoading || state.listLoading || state.listError || !feedUpdatedAt) return;
    const rows:Row[]=loadedRows;
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
  },[loadedRows,feedReady,pagesLoading,state.listLoading,state.listError,feedUpdatedAt,loadExplanation]);

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
  const actions=React.useMemo(()=>{
    const latest=new Map<string,Row>();
    for(const action of [...proposalActions,...(state.actions?.items||[])]){
      if(!latest.has(action.action_id)||latest.get(action.action_id)!.action_revision<=action.action_revision){
        latest.set(action.action_id,action);
      }
    }
    return [...latest.values()];
  },[proposalActions,state.actions]);
  const data=React.useMemo(()=>dataset(loadedRows,contexts,overview,traces,proposals,actions,isEn,explanations),[loadedRows,contexts,overview,traces,proposals,actions,isEn,explanations]);
  const selectedExplanation=selectedId&&revision?explanations[explanationKey(selectedId,revision)]:undefined;
  const select=React.useCallback((id:string)=>{
    const wsState=engine.current?.state;
    const latest=loadedRowsRef.current.find((row:Row)=>row.incident_id===id);
    const staleDetail=Boolean(latest && wsState?.detail && wsState.detail.revision<latest.revision);
    if(wsState?.selected!==id||!wsState?.detail||staleDetail)void engine.current?.select(id);
  },[]);
  const gate=<Card className="p-6 sm:p-10 max-w-2xl mx-auto mt-8 space-y-5"><h2 className="text-xl font-semibold">{tr('Kết nối workspace vận hành','Connect to your operator workspace')}</h2><p className="text-sm text-slate-500">{tr('Đăng nhập để xem dashboard, trace waterfall và service map bằng dữ liệu thật.','Sign in for live dashboards, trace waterfalls and the service map.')}</p>{mode==='oidc'&&<Button onClick={async()=>{try{setLocale(locale);window.location.assign(await login.current.begin());}catch(e:any){setError(e.message);}}}><LogIn className="h-4 w-4"/>{tr('Đăng nhập Cognito','Sign in with Cognito')}</Button>}{mode==='manual'&&<form className="flex flex-wrap gap-3" onSubmit={async e=>{e.preventDefault();const input=tokenInput.current;if(!input?.value)return;const value=input.value;input.value='';await engine.current.connect(value);}}><input ref={tokenInput} type="password" autoComplete="off" aria-label="API access token" className="rounded-lg border px-3 py-2"/><Button type="submit">{tr('Kết nối','Connect')}</Button></form>}{mode==='loading'&&<p role="status">{tr('Đang kiểm tra đăng nhập…','Checking sign-in…')}</p>}{mode==='unavailable'&&<Notice error>{tr('Đăng nhập chưa được cấu hình.','Sign-in is not configured.')}</Notice>}<a href="/ui/demo/" className="text-sm text-orange-700 underline block">{tr('Mở giao diện mô phỏng gốc','Open the original simulation')}</a></Card>;
  const review=<div className="space-y-4"><div className="flex flex-wrap gap-3 justify-between"><h2 className="font-semibold">{tr('Duyệt proposal theo revision thật','Review the actual revision-bound proposal')}</h2><Button size="sm" variant="secondary" disabled={state.proposalsLoading||state.decisionLoading||!detail} onClick={()=>void engine.current.loadProposals()}>{tr('Tải lại đề xuất','Reload proposals')}</Button></div>{state.proposalsError&&<Notice error>{state.proposalsError.message} · {tr('Tải lại để xác minh kết quả trước khi quyết định tiếp.','Reload to verify the outcome before another decision.')}</Notice>}{state.proposalsLoading&&<p>{tr('Đang tải…','Loading…')}</p>}{state.proposals?.length===0&&<Notice>{tr('Revision này không có proposal để duyệt.','No proposal for this revision.')}</Notice>}{state.proposals?.map((row:Row)=><Proposal key={row.proposal.proposal_id} row={row} state={state} engine={engine.current} now={now} isEn={isEn} memoryReview={memoryReview.current} memoryRecoveryReview={memoryRecoveryReview.current} onDecided={()=>void readProposals()}/>)}</div>;
  const journal=<Card className="p-5 space-y-3"><h3 className="font-semibold">{tr('Nhật ký thực thi / Giải thích','Execution journal / Explanation')}</h3>{state.actionsError&&<Notice error>{state.actionsError.message}</Notice>}{state.actions?.items.map((item:Row)=><details key={item.action_id}><summary className="cursor-pointer text-sm break-all">{item.phase} · {item.action_id}</summary><Raw value={item}/></details>)}{selectedExplanation?.aiExplanationStatus==='ready'&&<p className="whitespace-pre-wrap text-sm">{selectedExplanation.aiExplanation.summary}</p>}{selectedExplanation?.aiExplanationStatus==='error'&&<Notice error>{selectedExplanation.aiExplanationError}</Notice>}</Card>;
  const notices=<div className="space-y-3 mb-5">{error&&<Notice error>{error}</Notice>}{state.notice&&<Notice error>{state.notice}</Notice>}{state.listError&&<Notice error>{state.listError.message}</Notice>}{pagesError&&<Notice error>{pagesError}</Notice>}{watchError&&<Notice error>{watchError}</Notice>}{telemetryError&&<Notice error>{telemetryError}</Notice>}{traceError&&state.session&&<Notice>{tr('Trace: ','Trace: ')}{traceError}</Notice>}{state.session&&<p className="text-xs text-slate-500">{tr('Telemetry từ Prometheus; incident trong feed đã tải. Cạnh trace theo span đã ghi nhận.','Telemetry from Prometheus; incidents from loaded feed. Trace edges follow recorded spans.')} {overview?.as_of&&`${tr('Đo lúc','Observed')} ${stamp(overview.as_of,locale)}`}{pagesLoading&&` · ${tr('Đang tải toàn bộ incident…','Loading all incidents…')}`}{remainingCursor&&!pagesLoading&&<Button size="sm" variant="ghost" onClick={()=>void loadMorePages()}>{tr('Tải thêm incident','Load more incidents')}</Button>}</p>}</div>;
  return <App live={{...data,connected:state.session,selectedId:state.selected||'',onSelect:select,gate,
    header:state.session?<Button size="sm" variant="ghost" onClick={disconnect}><LogOut className="h-4 w-4"/>{tr('Ngắt kết nối','Disconnect')}</Button>:null,
    notice:notices,reviewPanel:review,journal,trend:data.trend,
    onRefresh:()=>{void engine.current?.loadList();void readProposals();if(detail)void engine.current.select(detail.incident_id);},onRecentTraces:()=>void loadTraces(true)}}/>;
}
