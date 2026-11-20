"""Add OOM review to the two inspected live React bundles, keeping their design.

Usage: python scripts/patch_oom_demo_ui.py EXTRACTED_ASSETS SOURCE_ASSETS
The extracted directory comes from the existing operator-ui image. This tool
changes two proposal cards, the shared API client/workspace and points the main
index at the existing live React entrypoint. The separate /ui/demo/ simulation,
routes, CSS, navigation and unrelated components are preserved. Unknown
bundle versions fail before any write. It is a temporary compatibility bridge
until the separately maintained React sources are available in this checkout.
"""
from pathlib import Path
import argparse
import hashlib
import shutil

ORIGINAL_HASHES = {
    'LiveApp-C3gAkFR8.js': 'afec4f264e446173ba38a5fac1cc3e00f2b902a07454f694ca176782bbdb7eb2',
    'LiveApp-hLXRSc0f.js': '39e597ad4d7d1dd65b2af2b7c0045a8050c077c92ee953970c49608eae7efe34',
}

TEMPLATE = '''function FUNCTION({row,state,engine,now,isEn}) {
  const [reason,setReason]=REACT.useState(''), p=row.proposal, images=row.review_images;
  const memory=oomMemoryReview(row), isMemory=p.executor_runbook==='pod-memory-limit-demo/v1';
  const remaining=Math.max(0,Math.ceil((Date.parse(p.expires_at)-now)/1000));
  const current=row.current_revision===p.incident_revision;
  const pending=row.status==='pending'&&remaining>0&&current&&now>=Date.parse(p.created_at);
  const enabled=pending&&state.canApprove&&!state.decisionLoading;
  const complete=isMemory?!!memory:!!(images?.current_image&&images?.rollback_image);
  const label=(vi,en)=>isEn?en:vi;
  const submit=async decision=>{
    if(!reason.trim()||!enabled||decision==='approve'&&!complete)return;
    if(window.confirm(`${label('Xác nhận','Confirm')} ${decision}: ${p.runbook_id}\\n${p.incident_id} / r${p.incident_revision}\\n${reason.trim()}`))
      await engine.decideProposal(p.proposal_id,decision,reason.trim());
  };
  const items=[[label('Sự cố / revision','Incident / revision'),`${p.incident_id} / r${p.incident_revision}`]];
  if(isMemory)items.push(
    [label('Nguyên nhân Kubernetes','Kubernetes failure'),memory?.cause||label('Bằng chứng chưa đầy đủ','Incomplete evidence')],
    [label('Pod gặp lỗi (UID)','Failed pod UID'),memory?.pod_uid],
    [label('Giới hạn RAM','Memory limit'),memory?`${memory.memory_before} → ${memory.memory_after}`:'—'],
    ['Deployment',memory?.deployment],[label('Image giữ nguyên','Image unchanged'),memory?.image],
    [label('Điều kiện hồi phục','Recovery checks'),label('Pod mới Ready, không restart, HTTP ổn định 30 giây','Replacement ready, zero restarts, HTTP healthy for 30 seconds')]);
  else items.push([label('Điểm khớp / margin','Match score / margin'),`${p.score} / ${p.margin}`],
    [label('Image hiện tại','Current image'),images?.current_image],[label('Image rollback','Rollback image'),images?.rollback_image]);
  items.push([label('Hạn duyệt','Approval deadline'),DATE(p.expires_at)],['SHA-256',row.proposal_sha256]);
  return JSX.jsxs(CARD,{className:'p-5 space-y-4',accent:pending?'orange':'none','data-testid':'live-proposal',children:[
    JSX.jsxs('div',{className:'flex flex-wrap justify-between gap-3',children:[
      JSX.jsx('h3',{className:'font-semibold break-all',children:p.runbook_id}),
      JSX.jsx(BADGE,{tone:row.status==='recovered'?'low':pending?'high':'neutral',children:row.status})]}),
    JSX.jsx(FACTS,{items}),
    pending&&JSX.jsxs('p',{className:'text-sm font-semibold text-orange-700',children:[label('Thời gian còn lại','Time remaining'),': ',Math.floor(remaining/60),':',String(remaining%60).padStart(2,'0')]}),
    !current&&row.status==='pending'&&JSX.jsx(CALLOUT,{children:label('Incident đã có revision mới. Hãy tải lại incident hiện tại.','Incident revision advanced. Reload the current incident.')}),
    !state.canApprove&&JSX.jsx(CALLOUT,{children:label('Phiên chỉ đọc: cần quyền remediation.approve để quyết định.','Read-only session: remediation.approve permission is required.')}),
    row.approval&&JSX.jsx(RECEIPT,{value:row.approval}),
    row.memory_action&&JSX.jsxs(CALLOUT,{error:['blocked','escalated'].includes(row.memory_action.phase),children:[label('Hồi phục: ','Recovery: '),row.memory_action.phase,' · ',row.memory_action.reason]}),
    row.outcome&&!row.memory_action&&JSX.jsxs(CALLOUT,{error:true,children:[row.outcome.phase,': ',row.outcome.reason]}),
    JSX.jsx('p',{className:'text-xs text-slate-500',children:isMemory?
      label('Dựa trên OOMKilled do Kubernetes ghi nhận. Controller kiểm tra lại đúng pod, image và giới hạn RAM trước khi thực thi.','Based on the OOMKilled observed by Kubernetes. The controller rechecks the pod, image and memory limit before execution.'):
      label('Duyệt ghi nhận quyết định của người trực. Controller tiếp tục kiểm tra tuổi bằng chứng, target, quarantine và telemetry hồi phục.','Approval records a human decision. The controller still checks evidence freshness, target, quarantine and recovery telemetry.')}),
    JSX.jsxs('form',{onSubmit:event=>event.preventDefault(),className:'space-y-3',children:[
      JSX.jsxs('label',{className:'block text-sm font-medium',children:[label('Lý do quyết định','Decision reason'),
        JSX.jsx('textarea',{'aria-label':label('Lý do quyết định','Decision reason'),value:reason,onChange:event=>setReason(event.target.value),maxLength:2000,rows:2,className:'block w-full mt-2 rounded-lg border border-slate-300 p-3'})]}),
      JSX.jsxs('div',{className:'flex flex-wrap gap-3',children:[
        JSX.jsx(BUTTON,{disabled:!enabled||!complete||!reason.trim(),onClick:()=>void submit('approve'),children:label('Duyệt','Approve')}),
        JSX.jsx(BUTTON,{variant:'secondary',disabled:!enabled||!reason.trim(),onClick:()=>void submit('reject'),children:label('Từ chối','Reject')})]})]})]});
}
'''


def apply(assets, source):
    assets, source = Path(assets), Path(source)
    index = assets / 'index.html'
    html = index.read_text()
    old_entry, live_entry = 'index-BPzfiyKa.js', 'index-Ct_H0kkG.js'
    old_css, live_css = 'index--n1vkknQ.css', 'index-BiMCODzj.css'
    if old_entry in html:
        if html.count(old_entry) != 1 or html.count(old_css) != 1:
            raise ValueError('unreviewed UI index entrypoint')
        html = html.replace(old_entry, live_entry).replace(old_css, live_css)
    elif html.count(live_entry) != 1 or html.count(live_css) != 1:
        raise ValueError('unreviewed UI index entrypoint')
    if not (assets / live_entry).is_file() or not (assets / live_css).is_file():
        raise ValueError('live React entrypoint assets are missing')
    # Inspect every target before editing any file. Do not guess minified names
    # after a new design build or silently replace the application's entrypoint.
    for filename, expected in ORIGINAL_HASHES.items():
        path = assets / filename
        if path.exists() and 'oomMemoryReview' not in path.read_text() and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('unreviewed live UI bundle: ' + filename)
    if not any((assets / filename).is_file() for filename in ORIGINAL_HASHES):
        raise ValueError('no inspected live UI bundle exists')
    for filename, bindings in {
        'LiveApp-C3gAkFR8.js': dict(FUNCTION='Be',REACT='o',JSX='e',CARD='b',BADGE='k',FACTS='ge',DATE='S',CALLOUT='d',RECEIPT='F',BUTTON='m'),
        'LiveApp-hLXRSc0f.js': dict(FUNCTION='De',REACT='u',JSX='t',CARD='ae',BADGE='_e',FACTS='Re',DATE='pe',CALLOUT='L',RECEIPT='me',BUTTON='X'),
    }.items():
        path=assets/filename
        if not path.exists(): continue
        text=path.read_text()
        if 'oomMemoryReview' in text: continue
        start=text.index('function '+bindings['FUNCTION']+'(')
        end=text.index('function ',start+10)
        component=TEMPLATE
        import re
        component=re.sub(r'\b('+'|'.join(bindings)+r')\b',lambda m:bindings[m[0]],component)
        text='import {memoryReview as oomMemoryReview} from "./client.js";\n'+text[:start]+component+text[end:]
        path.write_text(text)
    # These shared modules are imported by the current live React application.
    for name in ('client.js','workspace.js'):
        shutil.copy2(source/name,assets/name)
    index.write_text(html)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('assets');p.add_argument('source');a=p.parse_args()
    apply(a.assets,a.source)
