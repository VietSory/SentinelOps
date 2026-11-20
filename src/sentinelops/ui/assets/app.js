import {Workspace, proposalRemaining} from './workspace.js';
import {CognitoLogin, IncidentClient, memoryReview, memoryRunbook} from './client.js';
import {mount as mountDeeplinks} from './deeplinks.js';
import {mount as mountFilters} from './filters.js';
import {mount as mountSummary} from './summary.js';
import {mount as mountProposalwatch} from './proposalwatch.js';

const loginCallback = location.pathname === '/ui/callback' ? location.href : null;
if (loginCallback) history.replaceState(null,'','/ui/');
let cognito = null, manualAllowed = false;

let language = new URLSearchParams(location.hash.slice(1)).get('lang') === 'vi' ? 'vi' : 'en';
const vi = {
  "Loading records…": "Đang tải bản ghi…",
  "Unavailable": "Unavailable",
  "Token was rejected. Connect again with a valid access token.": "Token bị từ chối. Kết nối lại bằng token hợp lệ.",
  "This token cannot read incidents. Connect with a token that has incident read permission.": "Token này không có quyền đọc incident. Kết nối bằng token có quyền đọc incident.",
  "Authentication is unavailable. Check the API identity configuration or retry when its provider is available.": "Không thể xác thực. Kiểm tra cấu hình định danh API hoặc thử lại khi provider khả dụng.",
  "Authentication is unavailable for this environment. Check its identity adapter.": "Môi trường này không thể xác thực. Kiểm tra identity adapter.",
  "The incident store is unavailable. Previously loaded data may be stale.": "Kho incident không khả dụng. Dữ liệu đã tải có thể đã cũ.",
  "This incident or revision could not be found.": "Không tìm thấy incident hoặc revision này.",
  "Action history is unavailable. This does not establish that no action occurred.": "Lịch sử hành động không khả dụng. Điều này không có nghĩa chưa có hành động xảy ra.",
  "Action history could not be verified. Reload when its evidence is available.": "Không thể xác minh lịch sử hành động. Tải lại khi có bằng chứng.",
  "Explanation is unavailable for this revision. The incident and evidence remain readable.": "Revision này chưa có giải thích khả dụng. Vẫn đọc được incident và bằng chứng.",
  "The API did not respond in time. Try again.": "API không phản hồi kịp. Vui lòng thử lại.",
  "Cannot reach the API. Check the connection and try again.": "Không kết nối được API. Kiểm tra kết nối và thử lại.",
  "The API returned an unsupported response. Refresh or check its version.": "API trả về phản hồi không được hỗ trợ. Làm mới hoặc kiểm tra phiên bản.",
  "The request could not be completed. Try again.": "Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
  "Unknown": "Unknown",
  "Approval is an external CLI decision; this journal only shows recorded observations.": "Duyệt là quyết định bên ngoài qua CLI; nhật ký này chỉ hiển thị quan sát đã ghi.",

  "Skip to incidents": "Đến danh sách incident",
  "Workspace navigation": "Điều hướng không gian vận hành",
  "LOCAL WORKSPACE": "KHÔNG GIAN CỤC BỘ",
  "OPERATIONS": "VẬN HÀNH",
  "Incidents": "Các incident",
  "Explanations": "Giải thích incident",
  "Action history": "Lịch sử hành động",
  "In detail": "Trong hồ sơ",
  "Evidence first.": "Bằng chứng trước tiên.",
  "Observe what happened.": "Quan sát điều đã xảy ra.",
  "Keep uncertainty visible.": "Luôn hiển thị điều chưa biết.",
  "OPERATOR CONSOLE": "GIAO DIỆN VẬN HÀNH",
  "Operator workspace": "Không gian vận hành",
  "Not connected": "Chưa kết nối",
  "Disconnect": "Ngắt kết nối",
  "Connected · operator console": "Đã kết nối · giao diện vận hành",
  "EVIDENCE → DECISION → VERIFICATION": "BẰNG CHỨNG → QUYẾT ĐỊNH → XÁC MINH",
  "The recovery story": "Hành trình hồi phục",
  "Follow the incident from the first signal to verified recovery. Uncertainty stays visible.": "Theo dõi incident từ tín hiệu đầu tiên đến xác minh hồi phục. Luôn phân biệt điều chưa biết.",
  "Refresh": "Làm mới",
  "Inject fault": "Gây lỗi",
  "External scenario": "Kịch bản bên ngoài",
  "Detect": "Phát hiện",
  "Trigger provenance": "Nguồn kích hoạt",
  "Investigate": "Tìm nguyên nhân",
  "Ranked hypotheses": "Giả thuyết xếp hạng",
  "Record": "Ghi nhận",
  "IER · revision · evidence": "IER · revision · bằng chứng",
  "Explain": "Giải thích",
  "Provider identified": "Nêu rõ bộ giải thích",
  "Review rollback": "Duyệt rollback",
  "Operator · UI": "Người vận hành · UI",
  "Restore image": "Khôi phục image",
  "Action journal": "Nhật ký hành động",
  "Verify recovery": "Xác minh hồi phục",
  "Recovery source": "Nguồn xác nhận hồi phục",
  "Connect to your workspace": "Kết nối không gian vận hành",
  "Use your operator token or OIDC access token to read persisted incidents. It stays in this tab only.": "Dùng operator token hoặc OIDC access token để đọc incident đã lưu. Token chỉ nằm trong tab này.",
  "API access token": "Token truy cập API",
  "Paste API access token": "Dán token truy cập API",
  "Connect": "Kết nối",
  "Incident store": "Kho incident",
  "Readiness · dependency check": "Readiness · kiểm tra thành phần",
  "Model readiness": "Readiness mô hình",
  "Availability · not diagnostic accuracy": "Khả dụng · không phải độ chính xác chẩn đoán",
  "EVIDENCE RULE": "NGUYÊN TẮC BẰNG CHỨNG",
  "UNKNOWN means insufficient evidence, not healthy.": "UNKNOWN (chưa biết) nghĩa là thiếu bằng chứng, không phải khỏe mạnh.",
  "Incident records": "Bản ghi incident",
  "Persisted records": "Bản ghi đã lưu",
  "State": "Trạng thái",
  "Service": "Dịch vụ",
  "All states": "Tất cả trạng thái",
  "All services": "Tất cả dịch vụ",
  "Apply filters": "Lọc danh sách",
  "Waiting for connection": "Chờ kết nối",
  "Load more": "Tải thêm",
  "A live current-state view. Filters can change as records are revised.": "Danh sách trạng thái hiện tại. Kết quả lọc có thể thay đổi khi bản ghi được cập nhật revision.",
  "Evidence-driven reliability": "Độ tin cậy dựa trên bằng chứng",
  "Review proposals · Approve or reject": "Xem đề xuất · Duyệt hoặc từ chối",
  "Your incident feed starts here": "Bắt đầu từ danh sách incident",
  "Connect to read the incidents persisted by your health and incident workers.": "Kết nối để đọc incident do health worker và incident worker ghi lại.",
  "Loading incidents…": "Đang tải incident…",
  "Feed unavailable": "Không thể tải danh sách",
  "No incidents in this view": "Không có incident trong bộ lọc này",
  "Reading persisted records from the incident API.": "Đang đọc bản ghi đã lưu từ API incident.",
  "The request failed. Retry when the store is available; an outage does not mean the system is healthy.": "Yêu cầu thất bại. Thử lại khi kho khả dụng; mất kết nối không có nghĩa hệ thống khỏe mạnh.",
  "No matching records were returned. This alone does not establish service health.": "Không có bản ghi phù hợp được trả về. Điều này không đủ để kết luận dịch vụ khỏe mạnh.",
  "Persisted incident records in the current view": "Bản ghi incident đã lưu trong danh sách hiện tại",
  "Incident / services": "Incident / dịch vụ",
  "Severity": "Mức độ",
  "No services recorded": "Chưa ghi dịch vụ",
  "Affected services": "Dịch vụ bị ảnh hưởng",
  "Detection timestamp": "Thời điểm phát hiện",
  "Evidence availability": "Mức khả dụng bằng chứng",
  "Lifecycle": "Vòng đời",
  "Not recorded": "Chưa ghi nhận",
  "None recorded": "Chưa ghi nhận",
  "Revision recorded": "Thời điểm ghi revision",
  "Root-cause hypotheses": "Giả thuyết nguyên nhân gốc",
  "Diagnostic qualification not exposed": "Chưa có thông tin đủ điều kiện chẩn đoán",
  "Ranking is evidence support, not a confirmed cause. This API revision does not expose RCA coverage, margin or a qualified diagnostic decision.": "Xếp hạng thể hiện mức hỗ trợ từ bằng chứng, chưa xác nhận nguyên nhân. API revision này không cung cấp RCA coverage, margin hay quyết định chẩn đoán đủ điều kiện.",
  "No root-cause hypotheses were recorded.": "Chưa ghi nhận giả thuyết nguyên nhân gốc.",
  "Missing reference": "Thiếu tham chiếu",
  "Model signals": "Tín hiệu mô hình",
  "Recorded in this revision": "Được ghi trong revision này",
  "No model signals were recorded in this revision. Missing scores are unavailable, not zero.": "Revision này chưa ghi nhận tín hiệu mô hình. Điểm thiếu là không có dữ liệu, không phải bằng 0.",
  "Open Explanation for a summary of this revision’s recorded evidence. Controller guards still apply after approval.": "Mở Giải thích để xem tóm tắt bằng chứng của revision này. Controller vẫn kiểm tra guard sau khi duyệt.",
  "Source references are identifiers, not automatically downloadable links. Facts are shown exactly as recorded.": "Tham chiếu nguồn là định danh, không phải liên kết tải tự động. Sự kiện được hiển thị nguyên văn như đã ghi.",
  "No evidence was recorded in this revision.": "Revision này chưa ghi nhận bằng chứng.",
  "No service": "Chưa có dịch vụ",
  "Source reference unavailable": "Thiếu tham chiếu nguồn",
  "Copy reference": "Sao chép tham chiếu",
  "Copied": "Đã sao chép",
  "Select reference to copy": "Chọn tham chiếu để sao chép",
  "Revision source references": "Tham chiếu nguồn của revision",
  "Load more revisions": "Tải thêm revision",
  "Loading revisions…": "Đang tải revision…",
  "Reading explanation…": "Đang đọc giải thích…",
  "Deterministic fallback": "Deterministic fallback · bộ giải thích cố định, không phải LLM",
  "Provider-selected evidence": "Bằng chứng do provider chọn",
  "No bounded evidence claims are available for this revision.": "Revision này chưa có luận điểm gắn với bằng chứng.",
  "Loading explanation…": "Đang tải giải thích…",
  "Reload explanation": "Tải lại giải thích",
  "Load explanation": "Tải giải thích",
  "Reading action journal…": "Đang đọc nhật ký hành động…",
  "No action records were returned through this revision. This does not establish service health or recovery.": "Chưa có bản ghi hành động đến revision này. Điều này không xác nhận dịch vụ khỏe mạnh hay hồi phục.",
  "Previous image": "Image trước đó",
  "Known-good image": "Image tốt đã biết",
  "Manual quarantine review": "Rà soát quarantine thủ công",
  "Read-only observation": "Quan sát chỉ đọc",
  "Quarantine held": "Quarantine đang giữ",
  "Quarantine released by operator": "Quarantine được người vận hành giải phóng",
  "No quarantine claim recorded for this action": "Chưa ghi nhận quarantine cho hành động này",
  "This is the latest loaded status for this action. A released lock does not establish recovery or authorize another dispatch.": "Đây là trạng thái mới nhất đã tải của hành động này. Giải phóng khóa không xác nhận hồi phục hoặc cấp quyền dispatch lần nữa.",
  "Reading quarantine…": "Đang đọc quarantine…",
  "Reload quarantine status": "Tải lại trạng thái quarantine",
  "View quarantine status": "Xem trạng thái quarantine",
  "Loading action history…": "Đang tải lịch sử hành động…",
  "Reload action history": "Tải lại lịch sử hành động",
  "Load more actions": "Tải thêm hành động",
  "Retry incident": "Tải lại incident",
  "Reading incident evidence…": "Đang đọc bằng chứng incident…",
  "A closer look starts here": "Mở hồ sơ bằng chứng",
  "Loading a revision-pinned record and its lifecycle context.": "Đang tải bản ghi cố định theo revision và ngữ cảnh vòng đời.",
  "Choose an incident to explore its signals, root-cause hypotheses and source evidence.": "Chọn incident để xem tín hiệu, giả thuyết nguyên nhân và bằng chứng nguồn.",
  "INCIDENT DETAIL": "HỒ SƠ INCIDENT",
  "Revision": "Revision",
  "View": "Xem",
  "Latest revision": "Revision mới nhất",
  "Overview": "Tổng quan",
  "Timeline": "Dòng thời gian",
  "Explanation": "Giải thích",
  "Actions": "Hành động",
  "Choose an incident from the feed to view its explanation or action history.": "Chọn incident từ danh sách để xem giải thích hoặc lịch sử hành động.",
  "Connect to your workspace, then choose an incident to view its explanation or action history.": "Kết nối không gian vận hành, rồi chọn incident để xem giải thích hoặc lịch sử hành động.",
  "Enter an access token of at least 32 characters.": "Nhập token truy cập có ít nhất 32 ký tự.",
  "01–02 · Fault & detection": "01–02 · Gây lỗi & phát hiện",
  "03 · Investigate the cause": "03 · Tìm nguyên nhân",
  "04 · Persist the incident": "04 · Lưu incident",
  "05 · Explain the evidence": "05 · Giải thích bằng chứng",
  "06–07 · Review & restore": "06–07 · Duyệt & khôi phục",
  "08 · Verify recovery": "08 · Xác minh hồi phục",
  "Evidence level · recorded trigger": "Mức bằng chứng · nguồn kích hoạt đã ghi",
  "Evidence level · ranked hypothesis": "Mức bằng chứng · giả thuyết xếp hạng",
  "Evidence level · revision-pinned IER": "Mức bằng chứng · IER cố định theo revision",
  "Evidence level · generated summary": "Mức bằng chứng · tóm tắt được tạo",
  "Evidence level · action audit": "Mức bằng chứng · audit hành động",
  "Evidence level · lifecycle context": "Mức bằng chứng · ngữ cảnh vòng đời",
  "The fault scenario is external to this console. Trigger references below show what was recorded; they do not prove the fault injection itself.": "Kịch bản gây lỗi nằm ngoài giao diện. Tham chiếu kích hoạt cho biết nội dung đã ghi; không tự chứng minh thao tác gây lỗi.",
  "IER is the persisted incident evidence record; revision identifies the exact version being read.": "IER là bản ghi bằng chứng incident đã lưu; revision xác định chính xác phiên bản đang đọc.",
  "Review the runbook proposal above to approve or reject. Inspect Actions for the recorded execution and verification.": "Xem đề xuất runbook ở trên để duyệt hoặc từ chối. Mở Hành động để xem thực thi và xác minh đã ghi.",
  "Lifecycle and recovery_source are recorded context. A released quarantine lock alone is not recovery evidence.": "Lifecycle và recovery_source là ngữ cảnh đã ghi. Chỉ giải phóng khóa quarantine không phải bằng chứng hồi phục.",
  "UNKNOWN (unknown) · abstained (no decision) · ineligible (not qualified). These values never mean healthy.": "UNKNOWN (chưa biết) · abstained (không đưa ra quyết định) · ineligible (không đủ điều kiện). Các giá trị này không có nghĩa khỏe mạnh.",
  "Technical states stay unchanged: degrading → incident → recovering → recovered; escalated requires operator review.": "Giữ nguyên trạng thái kỹ thuật: degrading (suy giảm) → incident (sự cố) → recovering (đang hồi phục) → recovered (đã hồi phục); escalated (chuyển cấp) cần người vận hành rà soát.",
  "Fallback uses deterministic facts, not an LLM. Provider output is a separate explanation, not source evidence.": "Fallback dùng sự kiện theo quy tắc cố định, không phải LLM. Kết quả provider là giải thích riêng, không phải bằng chứng nguồn.",
  "Root-cause hypothesis": "Giả thuyết nguyên nhân gốc rễ",
  "AI-synthesized root cause": "AI tổng hợp nguyên nhân gốc rễ",
  "AI-generated explanation": "Lời giải thích do AI tổng hợp",
  "AI abstained": "AI không chọn giả thuyết",
  "Unknown": "Chưa xác định",
  "unavailable": "không có",
  " (not probability)": " (không phải xác suất)"
};
function t(value) {
  if (language !== 'vi' || typeof value !== 'string') return value;
  const text = value.trim();
  if (Object.hasOwn(vi,text)) return value.replace(text,vi[text]);
  const patterns = [
    [/^support (.+)$/,m => `mức hỗ trợ ${m[1]}`],
    [/^Anomaly (.+)$/,m => `Điểm bất thường ${m[1]}`],
    [/^Risk (.+)$/,m => `Rủi ro ${m[1]}`],
    [/^(.+) · emitted (.+)$/,m => `${m[1]} · phát lúc ${m[2]}`],
    [/^(\d+) loaded$/,m => `${m[1]} đã tải`],
    [/^(\d+) ranked$/,m => `${m[1]} giả thuyết`],
    [/^Evidence \((\d+)\)$/,m => `Bằng chứng (${m[1]})`],
    [/^Last loaded (.+)$/,m => `Tải lần cuối ${m[1]}`],
    [/^Recorded lifecycle events through revision (\d+)\. Recorded times are not inferred detection or recovery latencies\.$/,m => `Sự kiện vòng đời đã ghi đến revision ${m[1]}. Thời điểm ghi không phải độ trễ phát hiện hay hồi phục được suy ra.`],
    [/^Evidence summary for incident revision (\d+)\. Recorded evidence and ranked hypotheses do not establish a confirmed cause or action permission\.$/,m => `Tóm tắt bằng chứng cho incident revision ${m[1]}. Bằng chứng và giả thuyết xếp hạng không xác nhận nguyên nhân hay quyền hành động.`],
    [/^Action journal through incident revision (\d+)\. Action phases do not change the incident lifecycle\. This view cannot execute or approve a rollback\.$/,m => `Nhật ký hành động đến incident revision ${m[1]}. Action phase không thay đổi vòng đời incident. Xem panel đề xuất để quyết định duyệt.`],
    [/^Provider (.+) · generated (.+)$/,m => `Provider ${m[1]} · tạo lúc ${m[2]}`],
    [/^Recorded (.+) · updated (.+)$/,m => `Ghi lúc ${m[1]} · cập nhật ${m[2]}`],
    [/^Operator (.+) · recorded (.+)$/,m => `Người vận hành ${m[1]} · ghi lúc ${m[2]}`],
  ];
  for (const [pattern,format] of patterns) { const match=text.match(pattern); if (match) return format(match); }
  return value;
}

const $ = selector => document.querySelector(selector);
const node = (tag, text = null, className = '') => {
  const item = document.createElement(tag);
  if (text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
};
const raw = (tag,text,className = '') => { const item=node(tag,null,className); item.textContent=String(text); return item; };
const button = (text, action, className = 'button quiet') => {
  const item = node('button',text,className); item.type = 'button'; item.addEventListener('click',action); return item;
};
const stateLabels = {incident:'Incident',degrading:'Degrading',recovering:'Recovering',recovered:'Recovered',escalated:'Escalated'};
const severityLabels = {low:'Low',medium:'Medium',high:'High',critical:'Critical'};
const severityTones = {low:'good',medium:'warning',high:'serious',critical:'critical'};
const stateTones = {incident:'critical',degrading:'serious',recovering:'warning',recovered:'good',escalated:'critical'};
const statusIcons = {good:'✓',warning:'!',serious:'▲',critical:'✕',neutral:'?'};
const statusChip = (label, tone) => {
  const item = node('span',null,`status-chip status-chip--${tone}`);
  item.dataset.status = tone;
  const icon = node('span',statusIcons[tone],'status-chip__icon'); icon.setAttribute('aria-hidden','true');
  item.append(icon,node('span',label,'status-chip__label'));
  return item;
};
const badge = (value, kind) => {
  const labels = kind === 'severity' ? severityLabels : stateLabels;
  const tones = kind === 'severity' ? severityTones : stateTones;
  const known = Object.hasOwn(labels,value), tone = known ? tones[value] : 'neutral';
  const item = statusChip(known ? labels[value] : `Unknown ${kind}`,tone);
  item.title = `${kind === 'severity' ? 'Severity' : 'State'}: ${known ? labels[value] : value || 'not recorded'}`;
  return item;
};
const date = value => {
  if (typeof value !== 'string' || !value) return 'Unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Unavailable' : parsed.toLocaleString('en-US',{timeZone:'UTC',
    year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+' UTC';
};
const time = value => { const item = node('time',date(value)); if (value) item.dateTime = value; return item; };
const relativeTime = value => {
  const item = time(value), timestamp = typeof value === 'string' && value ? Date.parse(value) : NaN;
  item.title = date(value);
  if (!Number.isFinite(timestamp)) { item.textContent = 'Time unknown'; return item; }
  item.dataset.relativeTime = value;
  updateRelativeTime(item);
  return item;
};
function updateRelativeTime(item) {
  const delta = Date.now() - Date.parse(item.dataset.relativeTime), elapsed = Math.abs(delta);
  const unit = elapsed < 60000 ? 's' : elapsed < 3600000 ? 'm' : elapsed < 86400000 ? 'h' : 'd';
  const count = Math.floor(elapsed / ({s:1000,m:60000,h:3600000,d:86400000}[unit]));
  item.textContent = delta < 0 ? `in ${count}${unit}` : `${count}${unit} ago`;
}
const short = (value, width = 26) => value.length > width ? value.slice(0,width-7)+'…'+value.slice(-6) : value;
const score = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : 'Unavailable';
const serviceClass = value => ({'checkout-service':'service-checkout','payment-service':'service-payment','inventory-service':'service-inventory'}[value] || '');
const service = value => String(value).replace(/-service$/,'').replace(/^[a-z]/,letter => letter.toUpperCase());
const empty = (title, description, detail = false) => {
  const item = node('div',null,`empty ${detail ? 'detail-empty' : ''}`);
  const icon = node('div',detail ? '⌕' : '▤','empty-icon'); icon.setAttribute('aria-hidden','true');
  item.append(icon,node('h3',title),node('p',description)); return item;
};
function showError(container, error) {
  container.replaceChildren(); container.hidden = !error;
  if (!error) return;
  container.append(node('span',error.message));
  if (error.requestId) container.append(node('code',`Request ${error.requestId}`));
}
function status(container, value, yes, no) {
  const kind = value === true ? 'good' : value === false ? 'warning' : 'neutral';
  container.dataset.status = kind;
  const dot = node('span',null,`status-dot ${kind}`);
  container.replaceChildren(dot,document.createTextNode(value === true ? yes : value === false ? no : 'Unknown'));
}

let tab = 'overview', highlightedEvidence = null, previous = {};
let proposalClient = null, watcher = null, sessionAbort = new AbortController();
const handles = [];
const workspace = new Workspace(render,token => {
  proposalClient = new IncidentClient(token);
  return proposalClient;
});
const ctx = Object.freeze({
  get state() { return workspace.state; },
  getState: () => workspace.state,
  getDetail: () => workspace.state.detail,
  resetFilters: () => { tab = 'overview'; return workspace.filter({state:'',service:''}); },
  onChange: result => {
    renderList({...workspace.state,rows:result.rows});
    const unavailable = !result.total && ['error','disconnected','loading'].includes(result.status);
    $('#loaded-count').textContent = unavailable ? result.status === 'error' ? 'Results unavailable' : 'Unknown' :
      `${result.matched} of ${result.total}`;
  },
  onReview: async row => {
    await openIncident(row.proposal.incident_id,row.proposal.incident_revision);
    $('#detail-pane [data-testid=runbook-proposals]')?.scrollIntoView({block:'nearest'});
  },
  getRcaAsOf: row => {
    const clocks = (row.incident?.evidence || []).filter(fact => fact?.type === 'timeline' && fact.service === null &&
      typeof fact.fact === 'string' && fact.fact.startsWith('Source-verified continuous RCA hypotheses;') &&
      /^artifact:\/\/manifests\/[a-f0-9]{64}\.json$/.test(fact.source_ref)).map(fact => fact.observed_at);
    return clocks.length && clocks.every(value => typeof value === 'string' && Number.isFinite(Date.parse(value))) ?
      clocks.reduce((earliest,value) => Date.parse(value) < Date.parse(earliest) ? value : earliest) : null;
  },
  refresh,
  get signal() { return sessionAbort.signal; },
  client: Object.freeze({get: async (path, signal) => {
    if (path !== '/v1/runbook-proposals?limit=100') throw new TypeError('Unsupported proposal watch path');
    const client = proposalClient, session = sessionAbort;
    if (!client || !workspace.state.session || session.signal.aborted) throw new DOMException('Cancelled','AbortError');
    try {
      const body = await client.get(path,AbortSignal.any([session.signal,...(signal ? [signal] : [])]),{method:'GET'});
      if (session.signal.aborted || client !== proposalClient) throw new DOMException('Cancelled','AbortError');
      return body;
    } catch (error) {
      if ([401,403].includes(error.status) && client === proposalClient) workspace.disconnect(error.message);
      throw error;
    }
  }}),
});

function stopWatcher() {
  sessionAbort.abort();
  if (watcher) {
    watcher.destroy(); handles.splice(handles.indexOf(watcher),1); watcher = null;
  }
  proposalClient = null;
}

function refresh() {
  return Promise.all([workspace.loadList(),workspace.checkReadiness(),workspace.loadProposals()]);
}

function render(state) {
  if (previous.filters !== state.filters) {
    $('#state-filter').value = state.filters.state; $('#service-filter').value = state.filters.service;
  }
  $('#connection').hidden = state.session;
  $('#disconnect').hidden = !state.session;
  $('#workspace-status').hidden = $('#slot-summary').hidden = $('#incident-workspace').hidden = !state.session;
  $('#session-label').textContent = state.session ? 'Connected' : 'Not connected';
  $('#session-indicator .status-dot').className = `status-dot ${state.session ? 'good' : 'neutral'}`;
  $('#refresh').disabled = !state.session || state.listLoading;
  $('#refresh-label').textContent = state.listLoading ? 'Refreshing…' : 'Refresh';
  $('#state-filter').disabled = $('#service-filter').disabled = !state.session;
  $('#filter-form button[type=submit]').disabled = !state.session;
  $('#notice').textContent = state.notice || ''; $('#notice').hidden = !state.notice;
  const dependencies = state.readiness?.dependencies;
  status($('#store-status'),dependencies?.incident_store,'Available','Unavailable');
  status($('#model-status'),dependencies?.model,'Available','Unavailable');
  $('#readiness-error').textContent = state.readinessError?.message || '';
  const countsAvailable = state.session && !state.listError && Boolean(state.updatedAt) && (!state.listLoading || state.rows.length > 0);
  $('#loaded-count').textContent = countsAvailable ? `${state.rows.length} loaded` : '—';
  $('#load-more').hidden = !state.session || !state.nextCursor;
  $('#load-more').disabled = state.listLoading;
  $('#updated-at').textContent = state.listLoading ? 'Loading records…' :
    state.updatedAt ? `Last loaded ${date(state.updatedAt)}` : 'Waiting for records';
  showError($('#list-error'),state.listError);
  if (previous.rows !== state.rows || previous.selected !== state.selected || previous.session !== state.session ||
      previous.listLoading !== state.listLoading || previous.listError !== state.listError) renderList(state);
  if (previous.detail !== state.detail || previous.selected !== state.selected || previous.detailLoading !== state.detailLoading ||
      previous.detailError !== state.detailError || previous.historyLoading !== state.historyLoading ||
      previous.historyError !== state.historyError || previous.session !== state.session || previous.actions !== state.actions ||
      previous.actionsLoading !== state.actionsLoading || previous.actionsError !== state.actionsError ||
      previous.quarantines !== state.quarantines || previous.explanation !== state.explanation ||
      previous.explanationLoading !== state.explanationLoading || previous.explanationError !== state.explanationError ||
      previous.proposals !== state.proposals || previous.proposalsLoading !== state.proposalsLoading ||
      previous.proposalsError !== state.proposalsError || previous.decisionLoading !== state.decisionLoading) renderDetail(state);
  if (!state.session) stopWatcher();
  else if (!watcher) {
    sessionAbort = new AbortController();
    watcher = mountProposalwatch($('#slot-proposalwatch'),ctx); handles.push(watcher);
  }
  for (const handle of handles) handle.update?.(state) ?? handle.refresh?.(state);
  previous = {...state};
}

function renderList(state) {
  const target = $('#incident-list'); target.setAttribute('aria-busy',String(state.listLoading));
  target.replaceChildren();
  if (!state.session) return;
  if (!state.rows.length) {
    target.append(empty(state.listLoading ? 'Loading incidents…' : state.listError ? 'Feed unavailable' : 'No incidents in this view',
      state.listLoading ? 'Reading persisted records from the incident API.' : state.listError ?
        'The request failed. Retry when the store is available; an outage does not mean the system is healthy.' :
        'No matching records were returned. This alone does not establish service health.'));
    return;
  }
  const list = node('ul',null,'incident-queue'); list.setAttribute('aria-label','Loaded incidents');
  for (const [index,row] of state.rows.entries()) {
    const item = node('li'); item.dataset.testid = 'incident-row';
    const selected = row.incident_id === state.selected;
    const open = button('',() => openIncident(row.incident_id,row.revision),`incident-link incident-link--compact ${selected ? 'selected' : ''}`);
    open.dataset.severity = Object.hasOwn(severityTones,row.severity) ? row.severity : 'unknown';
    open.title = `${row.incident_id} · revision ${row.revision}`; open.setAttribute('aria-label',`Open ${row.incident_id}, revision ${row.revision}`);
    if (selected) open.setAttribute('aria-current','true');
    const heading = node('span',null,'queue-heading'), chevron = node('span','›','queue-chevron');
    chevron.setAttribute('aria-hidden','true');
    const id = raw('strong',short(row.incident_id,23)); id.title = row.incident_id;
    heading.append(id,chevron);
    const services = node('span',null,'row-services');
    for (const name of row.affected_services || []) {
      const chip = raw('span',service(name),'incident-service'); chip.title = name; services.append(chip);
    }
    if (!services.childNodes.length) services.append(node('span','Services unknown','incident-services-unknown'));
    const footer = node('span',null,'queue-badges');
    footer.append(badge(row.severity,'severity'),badge(row.state,'state'));
    services.id = `queue-services-${index}`;
    footer.id = `queue-status-${index}`;
    open.setAttribute('aria-describedby',`${services.id} ${footer.id}`);
    const detected = relativeTime(row.detected_at); detected.className = 'row-time';
    detected.setAttribute('aria-label',`Detected ${date(row.detected_at)}`);
    open.append(heading,services,footer,detected);
    item.append(open); list.append(item);
  }
  target.append(list);
}

async function openIncident(id, revision = null) {
  tab = 'overview'; highlightedEvidence = null;
  await workspace.select(id,revision);
  if (workspace.state.selected === id && workspace.state.detail) {
    const query = new URLSearchParams(location.hash.slice(1));
    query.set('incident',id); query.set('revision',String(workspace.state.detail.revision));
    history.replaceState(null,'',`#${query}`);
    $('#detail-title')?.focus({preventScroll:!matchMedia('(max-width: 760px)').matches});
    await workspace.loadProposals();
  }
}

function fact(label, value) {
  const item = node('div'); item.append(node('span',label,'fact-label'),raw('div',value,`fact-value ${['UNKNOWN','abstained','ineligible'].includes(value) ? 'uncertain' : ''}`)); return item;
}
function section(title, note = null) {
  const heading = node('div',null,'section-heading'); heading.append(node('h3',title));
  if (note) heading.append(node('span',note)); return heading;
}
function helpDisclosure(title, ...content) {
  const item = node('details',null,'help-disclosure');
  const body = node('div',null,'help-disclosure__body'); body.append(...content);
  const summary = node('summary'), icon = node('span','i','info-icon'); icon.setAttribute('aria-hidden','true');
  summary.append(icon,node('span',title));
  item.append(summary,body); return item;
}
function overview(detail) {
  const target = node('div'), payload = detail.payload, context = detail.context;
  const grid = node('div',null,'facts-grid overview-facts');
  grid.append(fact('Lifecycle',context.lifecycle_stage),
    fact('Evidence availability',context.availability),fact('Recovery source',context.recovery_source || 'Not recorded'),
    fact('Revision recorded',date(detail.recorded_at)));
  target.append(helpDisclosure('Revision context',grid));
  target.append(section('Root-cause hypotheses',`${payload.root_cause_ranking.length} ranked`));
  target.append(helpDisclosure('Diagnostic qualification not exposed',node('p',
    'The ranking shows relative evidence support. This revision omits RCA coverage, margin and a qualified diagnostic decision.')));
  if (!payload.root_cause_ranking.length) target.append(node('p','No root-cause hypotheses were recorded.','subtle'));
  payload.root_cause_ranking.forEach((candidate,index) => {
    const card = node('div',null,'hypothesis'), heading = node('div',null,'hypothesis-heading');
    heading.append(node('span',index+1,'rank'),raw('strong',candidate.service,`service-label ${serviceClass(candidate.service)}`),node('span',`support ${score(candidate.score)}`,'score'));
    card.append(heading);
    const refs = node('div',null,'reference-list');
    for (const reference of candidate.evidence_refs) {
      const exists = payload.evidence.some(item => item.id === reference);
      const link = button(exists ? short(reference,24) : 'Missing reference',() => {
        tab = 'evidence'; highlightedEvidence = reference; renderDetail(workspace.state);
        const selected = [...document.querySelectorAll('[data-evidence-id]')].find(item => item.dataset.evidenceId === reference);
        if (selected) { selected.open = true; selected.scrollIntoView({block:'nearest'}); selected.querySelector('summary')?.focus(); }
      },`reference ${exists ? '' : 'missing'}`);
      link.title = exists ? reference : `Not present in this revision: ${reference}`; refs.append(link);
    }
    card.append(refs); target.append(card);
  });
  target.append(section('Model signals','Recorded in this revision'));
  if (!payload.service_signals.length) target.append(node('p','No model signals were recorded in this revision. Missing scores are unavailable, not zero.','subtle'));
  for (const signal of payload.service_signals) {
    const item = node('div',null,'signal');
    item.append(raw('strong',signal.service,`service-label ${serviceClass(signal.service)}`),node('span',`Anomaly ${score(signal.anomaly_score)}`),node('span',`Risk ${score(signal.degradation_risk)}`));
    target.append(item);
  }
  target.append(section('Detection sources','Recorded triggers'));
  if (!context.trigger_sources.length) target.append(node('p','No detection triggers were recorded.','subtle'));
  for (const trigger of context.trigger_sources) {
    target.append(node('p',`${trigger.source.toUpperCase()} · emitted ${date(trigger.emitted_at)}`,'subtle'),
      raw('code',trigger.source_ref,'source'));
  }
  return target;
}

function evidence(detail) {
  const target = node('div');
  target.append(section('Evidence for this revision'));
  target.append(helpDisclosure('About evidence references',node('p','Source references are identifiers, not automatically downloadable links. Facts are shown exactly as recorded.','subtle')));
  if (!detail.payload.evidence.length) target.append(node('p','No evidence was recorded in this revision.','subtle'));
  for (const item of detail.payload.evidence) {
    const card = node('details',null,`evidence-card ${highlightedEvidence === item.id ? 'highlight' : ''}`);
    card.dataset.evidenceId = item.id;
    const summary = node('summary'); summary.append(node('span',item.type,'evidence-kind'),node('span',short(item.id,24)),node('span',item.service || 'No service','evidence-service'));
    const body = node('div',null,'evidence-body'); body.append(raw('p',item.fact));
    const source = node('code',item.source_ref || 'Source reference unavailable','source'); body.append(source);
    const meta = node('div',null,'evidence-meta'); meta.append(time(item.observed_at));
    if (item.source_ref) {
      const copy = button('Copy reference',async () => {
        try { await navigator.clipboard.writeText(item.source_ref); copy.textContent = 'Copied'; }
        catch { copy.textContent = 'Select reference to copy'; }
      }); meta.append(copy);
    }
    body.append(meta); card.append(summary,body); target.append(card);
  }
  target.append(section('Revision source references'));
  for (const reference of detail.context.source_refs) target.append(node('code',reference,'source'));
  return target;
}

function timeline(detail, state) {
  const target = node('div');
  target.append(helpDisclosure('About this timeline',node('p',`Recorded lifecycle events through revision ${detail.revision}. Recorded times are not inferred detection or recovery latencies.`,'subtle')));
  const list = node('ol',null,'timeline');
  for (const event of detail.timeline.events) {
    const item = node('li'), heading = node('div',null,'timeline-heading');
    heading.append(node('strong',event.lifecycle_stage),node('span',`r${event.revision}`,'count'));
    item.append(heading,time(event.recorded_at),raw('p',event.reason),node('span',event.actor,'actor')); list.append(item);
  }
  target.append(list);
  const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.historyError); target.append(error);
  if (detail.timeline.next_after_revision !== null) {
    const more = button(state.historyLoading ? 'Loading revisions…' : 'Load more revisions',async () => {
      await workspace.loadHistory(); $('#history-more')?.focus();
    },'button secondary'); more.id = 'history-more'; more.disabled = state.historyLoading; target.append(more);
  }
  return target;
}

function explanation(state) {
  const target = node('div'), result = state.explanation;
  target.setAttribute('aria-busy',String(state.explanationLoading));
  target.append(section('Evidence summary','Generated explanation'));
  target.append(helpDisclosure('About this explanation',node('p',`Evidence summary for incident revision ${state.detail.revision}. Recorded evidence and ranked hypotheses do not establish a confirmed cause or action permission.`,'subtle')));
  if (state.explanationError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.explanationError); target.append(error);
  }
  if (state.explanationLoading) target.append(node('p','Reading explanation…','subtle'));
  if (result) {
    target.append(section(result.fallback ? 'Deterministic fallback' :
      (result.payload.root_cause?.service ? 'AI-generated explanation' : 'AI abstained')),
      node('p',`Provider ${result.provider} · generated ${date(result.generated_at)}`,'subtle'),
      helpDisclosure('Generated summary',raw('p',result.payload.summary,'explanation-summary')));
    if (result.payload.root_cause) {
      const root = result.payload.root_cause;
      const card = node('div',null,'explanation-root-cause');
      card.append(node('h3',result.fallback ? 'Root-cause hypothesis' : 'AI-synthesized root cause'));
      card.append(node('p',`${root.service || 'Unknown'} · RCA score ${root.score === null ? 'unavailable' : score(root.score)} (not probability)`,'subtle'));
      card.append(raw('p',root.explanation));
      const refs = node('div',null,'reference-list');
      for (const ref of root.evidence_refs) {
        const link = button(short(ref,24),() => {
          tab = 'evidence'; highlightedEvidence = ref; renderDetail(workspace.state);
          const item = [...document.querySelectorAll('[data-evidence-id]')].find(value => value.dataset.evidenceId === ref);
          if (item) { item.open = true; item.scrollIntoView({block:'nearest'}); item.querySelector('summary')?.focus(); }
        },'reference'); link.title = ref; refs.append(link);
      }
      card.append(refs); target.append(card);
    }
    if (!result.payload.claims.length) target.append(node('p','No bounded evidence claims are available for this revision.','subtle'));
    for (const claim of result.payload.claims) {
      const card = node('div',null,'explanation-claim'); card.append(raw('p',claim.text));
      const refs = node('div',null,'reference-list');
      for (const ref of claim.evidence_refs) {
        const link = button(short(ref,24),() => {
          tab = 'evidence'; highlightedEvidence = ref; renderDetail(workspace.state);
          const item = [...document.querySelectorAll('[data-evidence-id]')].find(value => value.dataset.evidenceId === ref);
          if (item) { item.open = true; item.scrollIntoView({block:'nearest'}); item.querySelector('summary')?.focus(); }
        },'reference'); link.title = ref; refs.append(link);
      }
      card.append(refs); target.append(card);
    }
  }
  const reload = button(state.explanationLoading ? 'Loading explanation…' : result ? 'Reload explanation' : 'Load explanation',async () => {
    const {incident_id,revision} = state.detail;
    await workspace.loadExplanation();
    if (tab === 'explanation' && workspace.state.detail?.incident_id === incident_id && workspace.state.detail?.revision === revision) {
      $('#explanation-reload')?.focus();
    }
  },'button secondary');
  reload.id = 'explanation-reload'; reload.disabled = state.explanationLoading; target.append(reload);
  return target;
}

function actionHistory(state,opened) {
  const target = node('div');
  target.append(section('Action journal','Recorded execution and verification'));
  target.append(helpDisclosure('About action phases',node('p',`Action journal through incident revision ${state.detail.revision}. Action phases do not change the incident lifecycle. Review the proposal panel for its approval decision.`,'subtle')));
  if (state.actionsError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.actionsError); target.append(error);
  }
  if (state.actionsLoading) target.append(node('p','Reading action journal…','subtle'));
  if (state.actions && !state.actions.items.length) target.append(node('p','No action records were returned through this revision. This does not establish service health or recovery.','subtle'));
  for (const item of state.actions?.items || []) {
    const card = node('details',null,'evidence-card'), heading = node('summary');
    card.dataset.actionId=item.action_id;card.open=opened.has(item.action_id);
    heading.append(node('strong',item.phase),node('code',short(item.action_id)),node('span',`action r${item.action_revision} · incident r${item.incident_revision}`));
    card.append(heading);
    const body = node('div',null,'evidence-body');
    if (item.reason) body.append(raw('p',item.reason));
    body.append(node('p',item.runbook,'subtle'),node('p',`Recorded ${date(item.recorded_at)} · updated ${date(item.updated_at)}`,'subtle'));
    for (const [label,value] of [['Previous image',item.before_image],['Known-good image',item.known_good_image]]) {
      body.append(node('span',label,'fact-label'),node('code',value || 'Unavailable','source'));
    }
    const events = node('ol',null,'timeline');
    for (const event of item.history) {
      const row = node('li'); row.append(node('strong',`r${event.revision} · ${event.phase}`),time(event.recorded_at));
      if (event.reason) row.append(raw('p',event.reason)); events.append(row);
    }
    body.append(events);
    const quarantine=state.quarantines[item.action_id],result=quarantine?.result;
    body.append(section('Manual quarantine review','Read-only observation'));
    if (result) {
      const labels={held:'Quarantine held',released:'Quarantine released by operator',not_held:'No quarantine claim recorded for this action'};
      body.append(node('strong',labels[result.quarantine_state]),node('p',
        'This is the latest loaded status for this action. A released lock does not establish recovery or authorize another dispatch.','subtle'));
      if (result.resolution) {
        body.append(node('p',`Operator ${result.resolution.operator_actor} · recorded ${date(result.resolution.recorded_at)}`),
          node('code',result.resolution.resolution_id,'source'));
      }
    }
    if (quarantine?.error) {
      const error=node('div',null,'inline-error');error.setAttribute('role','alert');showError(error,quarantine.error);body.append(error);
    }
    const inspect=button(quarantine?.loading ? 'Reading quarantine…' : result ? 'Reload quarantine status' : 'View quarantine status',
      async () => {
        await workspace.loadQuarantine(item.action_id);
        if (tab==='actions' && workspace.state.detail?.incident_id===state.detail.incident_id &&
            workspace.state.detail?.revision===state.detail.revision) {
          document.querySelector(`[data-quarantine-action="${item.action_id}"]`)?.focus();
        }
      },'button secondary');
    inspect.dataset.quarantineAction=item.action_id;
    inspect.disabled=Boolean(quarantine?.loading);body.append(inspect);
    card.append(body); target.append(card);
  }
  const refresh = button(state.actionsLoading ? 'Loading action history…' : 'Reload action history',() => workspace.loadActions(),'button secondary');
  refresh.disabled = state.actionsLoading; target.append(refresh);
  if (state.actions?.next_cursor) {
    const more = button('Load more actions',() => workspace.loadActions(true),'button secondary');
    more.disabled = state.actionsLoading; target.append(more);
  }
  return target;
}

function runbookProposals(state, drafts) {
  const panel = node('section',null,'runbook-proposals');
  panel.setAttribute('aria-label','Runbook proposals'); panel.dataset.testid = 'runbook-proposals';
  const pending = state.proposals?.filter(row => row.status === 'pending' && proposalRemaining(row)).length || 0;
  panel.classList.toggle('has-pending',pending > 0);
  const heading = node('div',null,'proposal-heading');
  const title = node('div',null,'proposal-title');
  title.append(node('h3','Runbook proposals'));
  if (pending) title.append(statusChip(`${pending} awaiting decision`,'warning'));
  const reload = button('Reload proposals',() => workspace.loadProposals(),'button secondary');
  reload.disabled = state.proposalsLoading || state.decisionLoading;
  heading.append(title,reload); panel.append(heading);
  if (pending) panel.append(helpDisclosure('Approval safeguards',node('p','The controller checks all safeguards after approval. Rejection sends the proposal to an engineer.')));
  if (pending && !state.canApprove) panel.append(node('p','This session has read-only access. Decisions require the remediation.approve scope.','callout uncertain'));
  if (state.proposalsLoading) panel.append(node('p','Loading proposals…'));
  if (state.proposalsError) {
    const error = node('p',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.proposalsError); panel.append(error);
    panel.append(node('p','The outcome is unconfirmed. Reload the proposal before making another decision.','subtle'));
  }
  if (state.proposals?.length === 0) panel.append(node('p','No proposals for this revision.'));
  for (const row of state.proposals || []) {
    const p = row.proposal, card = node('article',null,'proposal-card');
    const grid = node('div',null,'facts-grid');
    const currentImage = row.review_images?.current_image;
    const targetImage = row.review_images?.rollback_image;
    const memory = memoryReview(row);
    const reviewComplete = p.executor_runbook === memoryRunbook ? Boolean(memory) : Boolean(currentImage && targetImage);
    grid.append(fact('Incident / revision',`${p.incident_id} / r${p.incident_revision}`),
      fact('Severity',p.symptoms?.severity || row.incident.severity),fact('Proposed runbook',p.runbook_id));
    if (memory) grid.append(fact('Observed failure',memory.cause), fact('Failed pod UID',memory.pod_uid),
      fact('Memory limit change',`${memory.memory_before} → ${memory.memory_after}`),
      fact('Deployment',memory.deployment),fact('Image stays unchanged',memory.image),
      fact('Recovery checks','Replacement ready, zero restarts, HTTP healthy for 30 seconds'));
    else grid.append(fact('Score',score(p.score)),fact('Margin',score(p.margin)),
      fact('Current image',currentImage || 'Image evidence unavailable'),
      fact('Rollback image',targetImage || 'Image evidence unavailable'));
    grid.append(
      fact('Approval deadline',date(p.expires_at)),fact('Status',row.status));
    card.append(grid,node('h4',memory ? 'Kubernetes evidence' : 'RCA top-3'));
    if (!reviewComplete) card.append(node('p','This proposal needs pinned change evidence for approval. You can reject it and send it to an engineer.','callout uncertain'));
    const ranking = node('ol');
    for (const item of row.incident.root_cause_ranking.slice(0,3)) ranking.append(raw('li',`${item.service} · ${score(item.score)}`));
    if (!ranking.childNodes.length) ranking.append(node('li',memory ?
      'OOMKilled was observed by Kubernetes. This demo uses a fixed memory allocation; no ML confidence is claimed.' :
      'No root-cause hypotheses recorded.'));
    card.append(ranking,raw('code',`SHA-256: ${row.proposal_sha256}`,'source'));
    const countdown = node('p',null,'proposal-countdown');
    countdown.dataset.expires = p.expires_at; countdown.dataset.status = row.status; card.append(countdown);
    if (row.approval) card.append(raw('p',`${row.approval.decision} · ${row.approval.approved_by} · ${date(row.approval.approved_at)} · ${row.approval.reason}`));
    if (row.status === 'engineer') card.append(node('p',row.approval?.decision === 'reject' ?
      'An engineer will review the rejected proposal. The controller will skip execution.' : 'The controller referred this proposal to an engineer. See the outcome for details.'));
    if (row.outcome) card.append(raw('p',`${row.outcome.phase}: ${row.outcome.reason}`));
    if (row.memory_action) card.append(raw('p',`Recovery: ${row.memory_action.phase} · ${row.memory_action.reason}`));
    const form = node('form',null,'proposal-decision'), label = node('label','Decision reason'), reason = node('textarea');
    reason.id = `reason-${p.proposal_id}`; reason.required = true; reason.maxLength = 2000; reason.rows = 2;
    reason.value = drafts.get(reason.id) || '';
    label.htmlFor = reason.id; form.append(label,reason);
    for (const [decision,labelText] of [['approve','Approve'],['reject','Reject']]) {
      const item = button(labelText,() => {
        if (form.reportValidity() && reason.value.trim()) workspace.decideProposal(p.proposal_id,decision,reason.value);
      },`button ${decision === 'approve' ? 'primary' : 'secondary'}`);
      item.dataset.decision = decision;
      const locked = !state.canApprove || state.decisionLoading || row.status !== 'pending' || decision === 'approve' && !reviewComplete;
      item.disabled = locked || !proposalRemaining(row);
      item.dataset.locked = String(locked); form.append(item);
    }
    form.addEventListener('submit',event => event.preventDefault());
    card.append(form); panel.append(card);
  }
  return panel;
}

function updateProposalClocks() {
  for (const clock of document.querySelectorAll('[data-expires]')) {
    const seconds = Math.max(0,Math.ceil((Date.parse(clock.dataset.expires)-Date.now())/1000));
    clock.textContent = clock.dataset.status === 'pending' ? seconds ?
      `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')} left to approve` : 'Approval window expired' : 'No longer awaiting approval';
    for (const item of clock.closest('article').querySelectorAll('[data-decision]')) item.disabled = item.dataset.locked === 'true' || seconds === 0;
  }
}

function renderDetail(state) {
  for (const [id,key] of [['nav-incidents','overview'],['nav-explanation','explanation'],['nav-actions','actions']]) {
    const active = (state.detail ? tab : 'overview') === key ||
      (key === 'overview' && !['explanation','actions'].includes(tab));
    const item = $('#'+id); item.classList.toggle('active',active);
    if (active) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
  }
  const target = $('#detail-pane');
  // Keep the mounted module and its handle alive when rebuilding the header.
  const deeplinks = $('#slot-deeplinks');
  if (target.contains(deeplinks)) target.before(deeplinks);
  const titleFocused = document.activeElement === $('#detail-title');
  const drafts = new Map([...target.querySelectorAll('.proposal-decision textarea')].map(item => [item.id,item.value]));
  const opened=new Set([...target.querySelectorAll('details[data-action-id][open]')].map(item => item.dataset.actionId));
  target.replaceChildren(); target.setAttribute('aria-busy',String(state.detailLoading));
  if (state.detailError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.detailError);
    target.append(error,button('Retry incident',() => openIncident(state.selected,state.selectedRevision),'button secondary')); return;
  }
  if (!state.detail) {
    target.append(empty(state.detailLoading ? 'Loading incident…' : 'Select an incident',
      state.detailLoading ? 'Reading the selected revision and its evidence.' :
        'Choose a record from the incident queue to review its evidence, timeline and recovery.',true)); return;
  }
  const detail = state.detail, payload = detail.payload;
  const header = node('div',null,'detail-header'), overline = node('div',null,'detail-overline');
  const back = button('← Back to queue',() => {
    ($('#incident-list [aria-current=true]') || $('#list-title')).focus();
  },'button quiet back-to-queue');
  overline.append(node('span','Incident details','detail-label'),back,node('span',`Revision ${detail.revision}`,'count'));
  const title = raw('h2',detail.incident_id); title.id = 'detail-title'; title.tabIndex = -1;
  if (detail.incident_id.length > 36) title.replaceChildren(document.createTextNode(detail.incident_id.slice(0,36)),
    node('wbr'),document.createTextNode(detail.incident_id.slice(36)));
  const titleRow = node('div',null,'incident-title-row'); titleRow.append(title,deeplinks);
  const summary = node('div',null,'detail-summary');
  summary.append(badge(payload.severity,'severity'),badge(payload.state,'state'));
  const essentials = node('dl',null,'incident-essentials');
  const affected = node('div'), serviceChips = node('dd',null,'incident-affected-services');
  for (const name of payload.affected_services) {
    const chip = node('span',service(name),'incident-service'); chip.title = name; serviceChips.append(chip);
  }
  if (!serviceChips.childNodes.length) serviceChips.append(node('span','Services unknown'));
  affected.append(node('dt','Affected services'),serviceChips);
  const detected = node('div'), detectedValue = node('dd');
  detectedValue.append(relativeTime(payload.detected_at),time(payload.detected_at));
  detected.append(node('dt','Detected'),detectedValue);
  essentials.append(affected,detected);
  header.append(overline,titleRow,summary,essentials);
  const form = node('form',null,'revision-controls'), label = node('label','Revision '), input = node('input');
  input.type = 'number'; input.min = '1'; input.max = String(Number.MAX_SAFE_INTEGER); input.step = '1'; input.value = String(detail.revision);
  input.id = 'revision-input'; label.htmlFor = input.id; form.append(label,input);
  const go = node('button','View','button secondary'); go.type = 'submit'; form.append(go,button('Latest revision',() => openIncident(detail.incident_id)));
  form.addEventListener('submit',event => { event.preventDefault(); if (form.reportValidity()) openIncident(detail.incident_id,Number(input.value)); });
  header.append(form); target.append(header);
  const tabs = node('div',null,'tabs'); tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','Incident sections');
  for (const [key,label] of [['overview','Overview'],['evidence',`Evidence (${payload.evidence.length})`],['timeline','Timeline'],['explanation','Explanation'],['actions','Actions']]) {
    const item = button(label,() => openSection(key),`tab ${tab === key ? 'active' : ''}`);
    item.dataset.tab = key; item.setAttribute('aria-pressed',String(tab === key)); tabs.append(item);
  }
  const content = node('div',null,'detail-content'); content.append(tab === 'evidence' ? evidence(detail) : tab === 'timeline' ? timeline(detail,state) : tab === 'explanation' ? explanation(state) : tab === 'actions' ? actionHistory(state,opened) : overview(detail));
  const ier = node('details',null,'ier-record'); ier.append(node('summary','Incident evidence record (JSON)'),raw('pre',JSON.stringify(payload,null,2)));
  target.append(tabs,runbookProposals(state,drafts),content,ier); updateProposalClocks();
  if (titleFocused) title.focus({preventScroll:true});
}

async function openSection(key) {
  if (!workspace.state.detail) {
    $('#notice').textContent = workspace.state.session ?
      'Choose an incident from the feed to view its explanation or action history.' :
      'Connect to your workspace, then choose an incident to view its explanation or action history.';
    $('#notice').hidden = false;
    const target = workspace.state.session ? $('#incident-list') : $('#api-token');
    if (workspace.state.session) target.tabIndex = -1;
    target.focus(); return;
  }
  tab = key; renderDetail(workspace.state);
  if (key === 'actions' && !workspace.state.actions && !workspace.state.actionsError) await workspace.loadActions();
  if (key === 'explanation' && !workspace.state.explanation && !workspace.state.explanationError) await workspace.loadExplanation();
  if (tab === key) document.querySelector(`[data-tab="${key}"]`)?.focus();
}

$('#nav-explanation').addEventListener('click',() => openSection('explanation'));
$('#nav-actions').addEventListener('click',() => openSection('actions'));
$('#nav-incidents').addEventListener('click',event => {
  event.preventDefault();
  tab = 'overview'; renderDetail(workspace.state);
  $(workspace.state.session ? '#list-title' : manualAllowed ? '#api-token' : '#cognito-login').focus();
});

$('#connect-form').addEventListener('submit',async event => {
  event.preventDefault(); if (!manualAllowed) return;
  const token = $('#api-token').value.trim(); $('#api-token').value = '';
  if (token.length < 32) { $('#notice').textContent = 'Enter an access token of at least 32 characters.'; $('#notice').hidden = false; return; }
  await workspace.connect(token);
  if (workspace.state.session && !workspace.state.listError) {
    const query = new URLSearchParams(location.hash.slice(1)), id = query.get('incident'), rev = query.get('revision');
    if (id && rev && /^[1-9][0-9]*$/.test(rev)) await openIncident(id,Number(rev));
  }
});
$('#disconnect').addEventListener('click',() => {
  cognito?.clear(); workspace.disconnect();
  history.replaceState(null,'',location.pathname);
  $('#api-token').value = ''; $(manualAllowed ? '#api-token' : '#cognito-login').focus();
});
$('#cognito-login').addEventListener('click',async () => {
  if (!cognito) return;
  $('#cognito-login').disabled = true;
  try { workspace.disconnect(); location.assign(await cognito.begin()); }
  catch { $('#auth-status').textContent = 'Sign-in could not start. Check whether your browser allows session storage and try again.'; $('#cognito-login').disabled = false; }
});
$('#refresh').addEventListener('click',refresh);
$('#load-more').addEventListener('click',() => workspace.loadList(true));
$('#filter-form').addEventListener('submit',event => { event.preventDefault(); tab = 'overview'; workspace.filter({state:$('#state-filter').value,service:$('#service-filter').value}); });
const checkVisibleSession = () => { if (!document.hidden) {
  workspace.checkReadiness();
  if (!document.activeElement?.closest('.proposal-decision')) workspace.loadProposals();
} };
setInterval(updateProposalClocks,1000);
setInterval(() => {
  for (const item of document.querySelectorAll('[data-relative-time]')) updateRelativeTime(item);
},30000);
setInterval(checkVisibleSession,30000);
document.addEventListener('visibilitychange',checkVisibleSession);
window.addEventListener('pagehide',() => {
  cognito?.suspend(); workspace.disconnect();
  for (const handle of handles) handle.destroy();
  handles.length = 0;
});
handles.push(mountDeeplinks($('#slot-deeplinks'),ctx),mountSummary($('#slot-summary'),ctx),mountFilters($('#slot-filters'),ctx));
workspace.emit();

async function initializeLogin() {
  try {
    const response = await fetch('/ui/auth-config',{cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('unavailable');
    const config = await response.json();
    if (config.mode === 'manual' && !loginCallback) {
      manualAllowed = true; $('#connect-form').hidden = false;
      $('#auth-status').textContent = 'Local or test mode. Your access token stays in this tab.'; return;
    }
    if (config.mode !== 'oidc') throw new Error('unavailable');
    cognito = new CognitoLogin(config); $('#cognito-login').hidden = false;
    $('#auth-status').textContent = 'Sign in with your operator account. Your access token stays in this tab.';
    if (loginCallback) {
      const session = await cognito.complete(loginCallback);
      cognito.expireAt(session.expiresAt,() => {
        workspace.disconnect('Your session expired. Sign in again.');
      });
      await workspace.connect(session.accessToken,{canApprove:session.canApprove});
    }
  } catch {
    $('#auth-status').textContent = 'Sign-in could not complete. Check the configuration or try again.';
  }
}
initializeLogin();
