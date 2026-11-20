import {formatVietnamClock, formatVietnamStamp} from '@/lib/utils';
import type {AiExplanation, Incident, RemediationProposal, ServiceHealthOverview, ServiceName, TraceRecord, TraceSpan} from '@/types/sentinelops';
type Row=Record<string,any>;
export const number=(v:any):number=>typeof v==='number'&&Number.isFinite(v)?v:Number.NaN;
const names:Record<string,ServiceName>={'checkout-service':'Checkout','payment-service':'Payment','inventory-service':'Inventory'};
const when=(at:number)=>formatVietnamClock(at*1000);

export type ExplanationLoad =
  | {aiExplanationStatus:'loading'}
  | {aiExplanationStatus:'ready'; aiExplanation:AiExplanation}
  | {aiExplanationStatus:'error'; aiExplanationError:string};

export const explanationKey=(id:string,revision:number)=>JSON.stringify([id,revision]);

export function mapExplanation(response:Row|null|undefined):AiExplanation {
  const payload=response?.payload;
  const root=payload?.root_cause;
  if (!response || payload?.schema_version!=='2.0' || !root ||
      typeof response.fallback!=='boolean' || typeof response.provider!=='string' || !response.provider ||
      typeof response.generated_at!=='string' || !Number.isFinite(Date.parse(response.generated_at)) ||
      !Number.isSafeInteger(response.revision) || response.revision<1 ||
      (root.service!==null && typeof root.service!=='string') ||
      (root.score!=null && (!Number.isFinite(root.score) || root.score<0 || root.score>1)) ||
      typeof root.explanation!=='string' || typeof payload.summary!=='string' ||
      !Array.isArray(root.evidence_refs) || root.evidence_refs.some((ref:unknown)=>typeof ref!=='string')) {
    throw new Error('Invalid RCA explanation response');
  }
  return {
    state:response.fallback?'fallback':root.service===null?'abstained':'ai',
    provider:response.provider,generatedAt:response.generated_at,revision:response.revision,
    service:root.service,score:root.score??null,narrative:root.explanation,
    summary:payload.summary,evidenceRefs:root.evidence_refs,
  };
}

export function incidentTime(detectedAt:string,startedAt?:string):number {
  const detected=Date.parse(detectedAt);
  const started=Date.parse(startedAt??'');
  if (Number.isFinite(detected)) return detected;
  return Number.isFinite(started)?started:Number.NEGATIVE_INFINITY;
}
export function traceRecord(trace:Row,association:string,isEn:boolean):TraceRecord {
  const raw:Row[]=trace.spans||[];if(!Array.isArray(raw)||!raw.length||raw.length>2000||raw.some(s=>!Number.isFinite(s.startTime)||!Number.isFinite(s.duration)||s.duration<0))throw new Error('Invalid recorded spans');const start=Math.min(...raw.map(s=>s.startTime));
  const lookup=new Map<string,Row>(raw.map(s=>[s.spanID,s]));
  const depth=(span:Row,seen=new Set<string>()):number=>{
    const parent=span.references?.find((r:Row)=>r.refType==='CHILD_OF'&&r.traceID===trace.traceID)?.spanID;
    if(!parent||seen.has(parent)||!lookup.has(parent))return 0;
    seen.add(parent);return 1+depth(lookup.get(parent)!,seen);
  };
  const spans:TraceSpan[]=raw.map((s):TraceSpan=>{
    const tags=Object.fromEntries((s.tags||[]).map((t:Row)=>[t.key,String(t.value)]));
    const rawCode=tags['http.response.status_code']||tags['http.status_code'];const code=Number(rawCode)>=100&&Number(rawCode)<=599?rawCode:'—';
    const error=tags.error==='true'||Number(code)>=500;
    return {id:s.spanID,parentId:s.references?.find((r:Row)=>r.refType==='CHILD_OF')?.spanID,
      service:trace.processes?.[s.processID]?.serviceName||'unknown',operation:s.operationName,
      offsetMs:(s.startTime-start)/1000,durationMs:s.duration/1000,statusCode:code,
      status:error?'error':s.duration>750000?'slow':'ok',depth:depth(s),tags};
  }).sort((a,b)=>a.offsetMs-b.offsetMs||a.depth-b.depth);
  const children=new Map<string,TraceSpan[]>();spans.forEach(s=>{if(s.parentId)children.set(s.parentId,[...(children.get(s.parentId)||[]),s]);});const ordered:TraceSpan[]=[],visited=new Set<string>();const walk=(span:TraceSpan)=>{if(visited.has(span.id))return;visited.add(span.id);ordered.push(span);(children.get(span.id)||[]).forEach(walk);};spans.filter(s=>!s.parentId||!lookup.has(s.parentId)).forEach(walk);spans.forEach(walk);
  const errors=spans.filter(s=>s.status==='error').length;
  return {id:trace.traceID,label:`${association==='recorded'?(isEn?'Recorded':'Đã ghi nhận'):association==='recent'?(isEn?'Recent · independent window':'Gần đây · cửa sổ riêng'):(isEn?'Incident window':'Cửa sổ sự cố')} · ${trace.traceID.slice(0,8)}`,
    capturedAt:new Date(start/1000).toISOString(),totalDurationMs:Math.max(0,...spans.map(s=>s.offsetMs+s.durationMs)),
    isAnomalous:spans.some(s=>s.status!=='ok'),source:'live',association,
    summaryVi:isEn?`${spans.length} observed spans, ${errors} with recorded errors. Edges follow captured parent references.`:`${spans.length} span đã ghi nhận, ${errors} span có lỗi. Cạnh đi theo parent reference thực tế.`,spans:ordered};
}
export function dataset(rows:Row[],contexts:Record<string,Row>,overview:Row|null,traces:Record<string,TraceRecord[]>,proposals:Row[],actions:Row[],isEn:boolean,explanations:Record<string,ExplanationLoad>={}){
  const services:ServiceHealthOverview[]=['checkout-service','payment-service','inventory-service'].map(service=>{
    const row=overview?.services?.find((s:Row)=>s.service===service),p95=number(row?.p95_ms),error=number(row?.error_pct);
    const status=!Number.isFinite(p95)||!Number.isFinite(error)?'unknown':error>5?'critical':p95>750?'degraded':'healthy';
    return {name:names[service],status,statusLabelVi:status==='unknown'?(isEn?'Unknown':'Chưa đọc được'):status,
      p95LatencyMs:p95,errorRatePct:error,sloSuccessPct:number(row?.success_pct),rps:number(row?.rps),sloLatencyTargetMs:750,
      activeIncidents:rows.filter(r=>r.affected_services?.includes(service)&&r.state!=='recovered').length,
      version:'—',noteVi:overview?`${isEn?'Observed':'Đo lúc'} ${formatVietnamStamp(overview.as_of,isEn?'en':'vi')}`:(isEn?'Waiting for Prometheus':'Đang chờ Prometheus')};
  });
  const remediations:RemediationProposal[]=proposals.map(row=>{
    const p=row.proposal;const action=actions.find(a=>a.incident_id===p.incident_id&&a.incident_revision===p.incident_revision);
    let status=row.status;
    if(action?.phase==='recovered')status='success';else if(action?.phase==='blocked'||action?.phase==='escalated')status='engineer';
    else if(action?.phase==='dispatching'||action?.phase==='verifying')status='running';
    if(row.approval?.decision==='reject')status='rejected';
    return {id:p.proposal_id,incidentId:p.incident_id,incidentTitle:p.incident_id,service:'Payment',
      runbookId:p.runbook_id,runbookName:p.runbook_id,summaryVi:row.outcome?.reason||p.reasons?.join(', ')||p.runbook_id,
      riskLevel:'unknown',status,confidence:Math.round(p.score*100),createdAt:p.created_at,expiresAt:p.expires_at,
      currentImage:row.review_images?.current_image,rollbackImage:row.review_images?.rollback_image,
      approvedBy:row.approval?.approved_by,approvedAt:row.approval?.approved_at,decisionReason:row.approval?.reason,
      steps:(action?.history||[]).map((event:Row,i:number)=>({order:i+1,title:event.phase,command:event.reason||'',status:'done'})),
      verificationChecks:[],verificationSummaryVi:row.outcome?.reason||action?.reason};
  });
  const incidents=rows.map((row):Incident=>{
    const storedContext=contexts[row.incident_id];
    const context=storedContext?.revision>=row.revision?storedContext:undefined;
    const payload=context?.payload||row,ranking:Row[]=payload.root_cause_ranking||[];
    const revision=context?.revision??row.revision;
    const detectedAt=payload.detected_at??row.detected_at;
    const startedAt=payload.started_at??row.started_at??detectedAt;
    const rawPrimary=ranking[0]?.service,primary=names[rawPrimary]||'Unknown';
    const detected=incidentTime(detectedAt,startedAt),history:Row[]=context?.timeline?.events||[];
    const allSeries: [number,number|null][]=overview?.series?.p95_ms?.[rawPrimary]||[];
    const points=allSeries.filter(([t])=>t*1000>=detected-600000&&t*1000<=detected+1200000);
    const errors=new Map<number,number>(overview?.series?.error_pct?.[rawPrimary]||[]);
    const prior=allSeries.filter(([t,v])=>t*1000<detected&&t*1000>=detected-600000&&v!==null);
    const baseline=prior.length?prior.reduce((sum,[,v])=>sum+number(v),0)/prior.length:NaN;
    const peak=points.length?Math.max(...points.map(([,v])=>number(v)).filter(Number.isFinite)):NaN;
    const errorRate=points.length?Math.max(...points.map(([t])=>number(errors.get(t))).filter(Number.isFinite)):NaN;
    const linked=remediations.find(r=>r.incidentId===row.incident_id);
    const title=payload.summary||`${row.affected_services.join(' / ')} · ${payload.state}`;
    return {id:row.incident_id,revision,title,shortSummaryVi:context?.reason||title,
      impactVi:payload.affected_services.join(', '),severity:payload.severity,status:payload.state,
      services:payload.affected_services.map((s:string)=>names[s]).filter(Boolean),primaryService:primary,
      environment:overview?.environment||'Unknown',startedAt,detectedAt,
      suspectedRootCause:rawPrimary||(isEn?'Not yet concluded':'Chưa có kết luận'),rcaConfidence:number(ranking[0]?.score)*100,
      rcaCoverage:number(payload.rca_coverage)*100,rcaAbstained:payload.rca_abstained??!ranking.length,
      rcaAbstainReasonVi:context?.reason|| (isEn?'Insufficient recorded RCA evidence':'Chưa đủ bằng chứng RCA được ghi nhận'),
      ...explanations[explanationKey(row.incident_id,revision)],
      hasRemediation:Boolean(linked),remediationId:linked?.id,
      metricsSnapshot:{p95LatencyMs:peak,baselineLatencyMs:baseline,errorRatePct:errorRate,sloHealthPct:100-errorRate,affectedRequestsPct:NaN},
      metricSeries:points.map(([t,v])=>({time:when(t),p95Ms:number(v),baselineMs:baseline,errorRate:number(errors.get(t))})),
      evidence:(payload.evidence||[]).map((e:Row)=>({id:e.id,category:({metric:'metrics',log:'logs',trace:'traces',deployment:'deployments',model:'signals',timeline:'signals'} as Record<string,string>)[e.type]||'signals',
        service:names[e.service]||e.service,title:e.type+' · '+e.service,annotationVi:e.fact,detail:e.fact,
        severity:'normal',observedAt:e.observed_at,sourceRef:e.source_ref,highlight:ranking[0]?.evidence_refs?.includes(e.id)})),
      traces:traces[row.incident_id]||[],hypotheses:ranking.map((r:Row,i:number)=>({id:r.service,rank:i+1,service:r.service,title:r.service,
        descriptionVi:(isEn?'Recorded RCA hypothesis: ':'Giả thuyết RCA đã ghi nhận: ')+r.service,
        confidence:number(r.score)*100,score:r.score,coverage:number(r.coverage)*100,isPrimary:i===0,
        supportingEvidenceIds:r.evidence_refs||[],signalsUsed:[]})),
      reasoningTimeline:history.map((e:Row,i:number)=>({step:i+1,time:e.recorded_at,stage:e.lifecycle_stage,summaryVi:e.reason,status:e.lifecycle_stage==='ESCALATED'?'warning':'verified'})),
      updateHistory:history.map(e=>({id:e.event_id,revision:e.revision,stage:e.lifecycle_stage,time:e.recorded_at,actor:e.actor,summaryVi:e.reason})),
      timingsMin:{mttd:NaN,mtti:NaN,mttr:null},source:'live'};
  }).sort((left,right)=>incidentTime(right.detectedAt,right.startedAt)-incidentTime(left.detectedAt,left.startedAt)||left.id.localeCompare(right.id));
  const bins=new Map<number,any>();
  const end=Date.now(),start=end-86400000;
  for(let i=0;i<24;i++){const t=Math.floor((start+i*3600000)/3600000)*3600000;bins.set(t,{slot:when(t/1000),incidents:0,latencyMs:null});}
  rows.forEach(r=>{const t=Math.floor(Date.parse(r.detected_at)/3600000)*3600000;if(bins.has(t))bins.get(t).incidents++;});
  Object.values(overview?.series?.p95_ms||{}).forEach((series:any)=>series.forEach(([t,v]:[number,number|null])=>{const bin=bins.get(Math.floor(t/3600)*3600000);if(bin&&v!==null)bin.latencyMs=Math.max(bin.latencyMs??0,v);}));
  return {incidents,remediations,services,trend:[...bins.values()]};
}
