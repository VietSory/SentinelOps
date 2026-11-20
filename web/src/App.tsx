import * as React from 'react';
import { MotionConfig, AnimatePresence, motion } from 'framer-motion';
import {
  LayoutDashboard,
  AlertOctagon,
  GitBranch,
  SearchCheck,
  Wrench,
  BarChart3,
  RotateCcw,
  Menu,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DashboardView } from '@/views/DashboardView';
import { IncidentsView } from '@/views/IncidentsView';
import { IncidentDetailView } from '@/views/IncidentDetailView';
import { EvidenceTraceView } from '@/views/EvidenceTraceView';
import { RcaView } from '@/views/RcaView';
import { RemediationView } from '@/views/RemediationView';
import { ReportsView } from '@/views/ReportsView';
import { getInitialDataset } from '@/data';
import { useLocale, type Locale } from '@/i18n/LocaleContext';
import { cn } from '@/lib/utils';
import type { Incident, RemediationProposal, ServiceHealthOverview, NavSection, RemediationStatus } from '@/types/sentinelops';

interface NavMeta {
  id: Exclude<NavSection, 'incident-detail'>;
  labelVi: string;
  labelEn: string;
  pageTitleVi: string;
  pageTitleEn: string;
  pageSubtitleVi: string;
  pageSubtitleEn: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_ITEMS: NavMeta[] = [
  {
    id: 'dashboard',
    labelVi: 'Bảng điều khiển',
    labelEn: 'Dashboard',
    pageTitleVi: 'Bảng điều khiển',
    pageTitleEn: 'Dashboard',
    pageSubtitleVi: 'Toàn cảnh sức khỏe hệ thống, sự cố ưu tiên, SLO và trạng thái Remediation.',
    pageSubtitleEn: 'System-wide health, priority incidents, SLO compliance, and remediation status.',
    icon: LayoutDashboard,
  },
  {
    id: 'incidents',
    labelVi: 'Sự cố',
    labelEn: 'Incidents',
    pageTitleVi: 'Quản lý Sự cố (Incidents)',
    pageTitleEn: 'Incidents',
    pageSubtitleVi: 'Lọc theo Severity, Status, Service, môi trường và mở hồ sơ điều tra chi tiết.',
    pageSubtitleEn: 'Filter by severity, status, service, environment, and open full incident records.',
    icon: AlertOctagon,
  },
  {
    id: 'evidence',
    labelVi: 'Bằng chứng & Trace',
    labelEn: 'Evidence & Trace',
    pageTitleVi: 'Bằng chứng & Dấu vết Trace',
    pageTitleEn: 'Evidence & Trace',
    pageSubtitleVi: 'Service Map, Trace Waterfall, Metrics, Logs và Deployment Changes.',
    pageSubtitleEn: 'Service dependency map, trace waterfall, metrics, logs, and deployment changes.',
    icon: GitBranch,
  },
  {
    id: 'rca',
    labelVi: 'Phân tích nguyên nhân',
    labelEn: 'Root Cause Analysis',
    pageTitleVi: 'Phân tích nguyên nhân (RCA)',
    pageTitleEn: 'Root Cause Analysis',
    pageSubtitleVi: 'Xếp hạng giả thuyết nguyên nhân gốc, độ tin cậy (confidence) và timeline suy luận.',
    pageSubtitleEn: 'Ranked root-cause hypotheses, confidence and coverage gates, and reasoning timeline.',
    icon: SearchCheck,
  },
  {
    id: 'remediation',
    labelVi: 'Khắc phục',
    labelEn: 'Remediation',
    pageTitleVi: 'Khắc phục & Runbook (Remediation)',
    pageTitleEn: 'Remediation',
    pageSubtitleVi: 'Phê duyệt runbook có kiểm soát, theo dõi thực thi và kiểm chứng SLO.',
    pageSubtitleEn: 'Controlled runbook approval, live execution progress, and post-remediation verification.',
    icon: Wrench,
  },
  {
    id: 'reports',
    labelVi: 'Báo cáo',
    labelEn: 'Reports & History',
    pageTitleVi: 'Báo cáo & Lịch sử vận hành',
    pageTitleEn: 'Reports & History',
    pageSubtitleVi: 'Xu hướng sự cố, MTTD / MTTI / MTTR, top services và tỷ lệ Remediation thành công.',
    pageSubtitleEn: 'Incident trends, MTTD / MTTI / MTTR metrics, top services, and remediation success rate.',
    icon: BarChart3,
  },
];

interface RemediationOverride {
  status: RemediationStatus;
  completedSteps: number; // -1 = none done yet, 0..N-1 = index done
  runningStep: number | null;
  approvedAt: string;
  decisionReason: string;
}

export interface LiveVisualBinding {
  incidents: Incident[]; remediations: RemediationProposal[]; services: ServiceHealthOverview[];
  connected: boolean; selectedId: string; onSelect: (id:string)=>void;
  gate: React.ReactNode; header: React.ReactNode; notice: React.ReactNode;
  reviewPanel: React.ReactNode; journal: React.ReactNode;
  trend: {slot:string;incidents:number;latencyMs:number|null}[];
  onRefresh:()=>void; onRecentTraces:()=>void;
}
export default function App({live}: {live?:LiveVisualBinding}) {
  const { locale, setLocale, isEn } = useLocale();
  const [section, setSection] = React.useState<NavSection>('dashboard');
  const [selectedIncidentId, setSelectedIncidentId] = React.useState<string>(live ? '' : 'INC-2026-1048');
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const [overrides, setOverrides] = React.useState<Record<string, RemediationOverride>>({});
  const timersRef = React.useRef<number[]>([]);

  const clearTimers = () => {
    for (const id of timersRef.current) window.clearTimeout(id);
    timersRef.current = [];
  };

  React.useEffect(() => clearTimers, []);

  // Derive localized incidents, remediations, and services while preserving live approval state across VI <-> EN switches
  const { incidents, remediations, services } = React.useMemo<{incidents:Incident[];remediations:RemediationProposal[];services:ServiceHealthOverview[]}>(() => {
    if(live) return {incidents:live.incidents,remediations:live.remediations,services:live.services};
    const base = getInitialDataset(locale);

    const nextRemediations = base.remediations.map((item) => {
      const ov = overrides[item.id];
      if (!ov) return item;
      const verified = ov.status === 'success';
      const rejected = ov.status === 'rejected';
      return {
        ...item,
        status: ov.status,
        approvedBy: isEn ? 'SRE Operator (On-call)' : 'Người vận hành (On-call)',
        approvedAt: ov.approvedAt,
        decisionReason: ov.decisionReason,
        verificationSummaryVi: verified
          ? isEn
            ? 'Verified across 2 consecutive SLO windows: Payment p95 dropped from 1,240 ms to 8.4 ms; Error Rate dropped from 18.4% to 0.12%; Checkout returned to normal.'
            : 'Xác minh thành công sau 2 cửa sổ đo liên tiếp: Payment p95 giảm từ 1.240 ms về 8,4 ms; Error Rate giảm từ 18,4% về 0,12%; Checkout trở lại bình thường.'
          : rejected
            ? isEn
              ? 'Automated proposal rejected by operator and escalated to engineer for manual handling.'
              : 'Đề xuất tự động đã bị từ chối bởi người vận hành và chuyển sang kỹ sư trực tiếp kiểm tra.'
            : item.verificationSummaryVi,
        steps: item.steps.map((s, idx) => ({
          ...s,
          status: (
            idx <= ov.completedSteps
              ? 'done'
              : ov.runningStep === idx
                ? 'running'
                : 'pending'
          ) as 'done' | 'running' | 'pending' | 'failed',
          durationSec: idx <= ov.completedSteps ? s.durationSec ?? 5 + idx * 4 : undefined,
        })),
      };
    });

    const recoveredIncidentIds = new Set(
      nextRemediations.filter((r) => overrides[r.id]?.status === 'success').map((r) => r.incidentId),
    );
    const paymentRecovered = nextRemediations.some(
      (r) => r.service === 'Payment' && overrides[r.id]?.status === 'success',
    );

    const nextIncidents = base.incidents.map((inc) => {
      if (!recoveredIncidentIds.has(inc.id)) return inc;
      const rem = nextRemediations.find((r) => r.incidentId === inc.id);
      return {
        ...inc,
        revision: inc.revision + 1,
        status: 'recovered' as const,
        resolvedAt: rem?.approvedAt ?? new Date().toISOString(),
        timingsMin: { ...inc.timingsMin, mttr: 12 },
        updateHistory: [
          ...inc.updateHistory,
          {
            id: `UPD-VERIFIED-${inc.id}`,
            revision: inc.revision + 1,
            stage: 'VERIFIED · CLOSED',
            time: rem?.approvedAt ?? new Date().toISOString(),
            actor: 'remediation-verifier',
            summaryVi: isEn
              ? 'Rollback to Known-Good v2.8.3 completed; SLO Latency & Error Rate verified within safe limits.'
              : 'Rollback về bản Known-Good v2.8.3 hoàn tất; xác minh SLO Latency & Error Rate đạt ngưỡng an toàn.',
            highlight: true,
          },
        ],
      };
    });

    const nextServices = base.services.map((svc) => {
      if (!paymentRecovered) return svc;
      if (svc.name === 'Payment') {
        return {
          ...svc,
          status: 'healthy' as const,
          statusLabelVi: isEn ? 'Healthy · Recovered' : 'Healthy · Đã hồi phục',
          p95LatencyMs: 8,
          errorRatePct: 0.1,
          sloSuccessPct: 99.9,
          activeIncidents: 0,
          version: 'v2.8.3 (gen 6)',
          noteVi: isEn
            ? 'Rolled back to Known-Good v2.8.3 and passed SLO verification.'
            : 'Đã rollback về Known-Good v2.8.3 và xác minh SLO đạt chuẩn.',
        };
      }
      if (svc.name === 'Checkout') {
        return {
          ...svc,
          status: 'degraded' as const,
          statusLabelVi: isEn ? 'Warning · Inventory Impact' : 'Warning · Ảnh hưởng từ Inventory',
          p95LatencyMs: 910,
          errorRatePct: 0.4,
          sloSuccessPct: 99.6,
          activeIncidents: 1,
          noteVi: isEn
            ? 'Payment 502 errors cleared; mild latency remains from Inventory.'
            : 'Lỗi 502 từ Payment đã chấm dứt; còn độ trễ nhẹ từ Inventory.',
        };
      }
      return svc;
    });

    return {
      incidents: nextIncidents,
      remediations: nextRemediations,
      services: nextServices,
    };
  }, [locale, isEn, overrides, live]);

  React.useEffect(()=>{
    if(live?.connected && incidents.length && !incidents.some(i=>i.id===selectedIncidentId) && !remediations.some(r=>r.incidentId===selectedIncidentId)){
      setSelectedIncidentId(incidents[0].id);live.onSelect(incidents[0].id);
    }
  },[live?.connected,incidents,remediations,selectedIncidentId]);
  const selectIncident=(id:string)=>{setSelectedIncidentId(id);live?.onSelect(id);};
  const demoMutated = Object.keys(overrides).length > 0;

  const navigate = (next: NavSection, incidentId?: string) => {
    if (incidentId) selectIncident(incidentId);
    setSection(next);
    setMobileNavOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const openIncident = (incidentId: string) => {
    selectIncident(incidentId);
    setSection('incident-detail');
    setMobileNavOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const resetState = () => {
    clearTimers();
    setOverrides({});
  };

  const handleApproveRemediation = (proposalId: string, reason: string) => {
    if(live)return;
    clearTimers();
    const target = remediations.find((r) => r.id === proposalId);
    if (!target) return;
    const approvedAt = new Date().toISOString();

    setOverrides((prev) => ({
      ...prev,
      [proposalId]: {
        status: 'running',
        completedSteps: -1,
        runningStep: 0,
        approvedAt,
        decisionReason: reason,
      },
    }));

    target.steps.forEach((_, stepIndex) => {
      const timer = window.setTimeout(() => {
        const isLast = stepIndex === target.steps.length - 1;
        setOverrides((prev) => ({
          ...prev,
          [proposalId]: {
            status: isLast ? 'success' : 'running',
            completedSteps: stepIndex,
            runningStep: isLast ? null : stepIndex + 1,
            approvedAt,
            decisionReason: reason,
          },
        }));
      }, (stepIndex + 1) * 900);
      timersRef.current.push(timer);
    });
  };

  const handleRejectRemediation = (proposalId: string, reason: string) => {
    if(live)return;
    setOverrides((prev) => ({
      ...prev,
      [proposalId]: {
        status: 'rejected',
        completedSteps: -1,
        runningStep: null,
        approvedAt: new Date().toISOString(),
        decisionReason: reason,
      },
    }));
  };

  const openIncidentsCount = incidents.filter((i) => i.status !== 'recovered').length;
  const pendingRemediationsCount = remediations.filter((r) => r.status === 'pending').length;

  const currentIncident =
    incidents.find((i) => i.id === selectedIncidentId) ?? incidents[0];
  const currentIncidentRemediation = remediations.find(
    (r) => r.incidentId === currentIncident?.id,
  );

  const activeNavId: Exclude<NavSection, 'incident-detail'> =
    section === 'incident-detail' ? 'incidents' : section;
  const currentMeta =
    NAV_ITEMS.find((item) => item.id === activeNavId) ?? NAV_ITEMS[0];

  const renderSidebarContent = () => (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className="px-5 py-5 border-b border-slate-200/80">
        <button
          type="button"
          onClick={() => navigate('dashboard')}
          className="flex items-center gap-3 text-left cursor-pointer group"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-orange-600 text-white font-bold text-base shadow-sm group-hover:bg-orange-700 transition-colors">
            S
          </span>
          <div>
            <span className="block text-base font-bold tracking-tight text-slate-900">
              SentinelOps
            </span>
            <span className="block text-[11px] font-medium text-slate-500">
              Incident Intelligence
            </span>
          </div>
        </button>
      </div>

      {/* Navigation */}
      <nav
        aria-label={isEn ? 'Primary navigation' : 'Điều hướng chính'}
        className="flex-1 px-3 py-4 space-y-1"
      >
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeNavId === item.id;
          const badgeCount =
            item.id === 'incidents'
              ? openIncidentsCount
              : item.id === 'remediation'
                ? pendingRemediationsCount
                : 0;

          return (
            <button
              key={item.id}
              type="button"
              onClick={() => navigate(item.id)}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'w-full flex items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition-colors cursor-pointer',
                active
                  ? 'bg-orange-50 text-orange-700 ring-1 ring-orange-200/80'
                  : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-900',
              )}
            >
              <span className="flex items-center gap-2.5 min-w-0">
                <Icon
                  className={cn(
                    'h-[18px] w-[18px] shrink-0',
                    active ? 'text-orange-600' : 'text-slate-400',
                  )}
                />
                <span className="truncate">{isEn ? item.labelEn : item.labelVi}</span>
              </span>

              {badgeCount > 0 && (
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[11px] font-bold tabular-nums',
                    item.id === 'remediation'
                      ? 'bg-orange-600 text-white'
                      : active
                        ? 'bg-orange-200/80 text-orange-900'
                        : 'bg-red-100 text-red-700',
                  )}
                >
                  {badgeCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    </div>
  );

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen bg-[#FAFAFB] text-slate-900 flex">
        {/* Desktop Sidebar */}
        <aside className="hidden lg:block w-64 shrink-0 border-r border-slate-200/90 bg-white sticky top-0 h-screen">
          {renderSidebarContent()}
        </aside>

        {/* Mobile Drawer */}
        <AnimatePresence>
          {mobileNavOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setMobileNavOpen(false)}
                className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[2px] lg:hidden"
              />
              <motion.aside
                initial={{ x: -280 }}
                animate={{ x: 0 }}
                exit={{ x: -280 }}
                transition={{ duration: 0.2, ease: 'easeOut' }}
                className="fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-slate-200 shadow-xl lg:hidden"
              >
                <div className="absolute right-3 top-4">
                  <button
                    type="button"
                    onClick={() => setMobileNavOpen(false)}
                    aria-label={isEn ? 'Close navigation menu' : 'Đóng menu điều hướng'}
                    className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"
                  >
                    <X className="h-5 w-5" />
                  </button>
                </div>
                {renderSidebarContent()}
              </motion.aside>
            </>
          )}
        </AnimatePresence>

        {/* Main Content Column */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* Topbar */}
          <header className="sticky top-0 z-30 border-b border-slate-200/90 bg-white/92 backdrop-blur-md">
            <div className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8 h-15 flex items-center justify-between gap-4">
              <div className="flex items-center gap-3 min-w-0">
                <button
                  type="button"
                  onClick={() => setMobileNavOpen(true)}
                  aria-label={isEn ? 'Open navigation menu' : 'Mở menu điều hướng'}
                  className="lg:hidden rounded-lg p-2 text-slate-600 hover:bg-slate-100 cursor-pointer"
                >
                  <Menu className="h-5 w-5" />
                </button>
                <div className="flex items-center gap-2 text-xs text-slate-500 truncate">
                  <span className="font-semibold text-slate-800">SentinelOps</span>
                  <span>/</span>
                  <span className="font-semibold text-orange-700 truncate">
                    {section === 'incident-detail'
                      ? `${isEn ? 'Incidents' : 'Sự cố'} / ${currentIncident?.id || ''}`
                      : isEn
                        ? currentMeta.labelEn
                        : currentMeta.labelVi}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2.5 shrink-0">
                <span className="rounded-md bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">
                  {live ? (live.connected ? (isEn?'Live data':'Dữ liệu trực tiếp') : (isEn?'Not connected':'Chưa kết nối')) : (isEn ? 'Demo data' : 'Dữ liệu mô phỏng')}
                </span>
                {pendingRemediationsCount > 0 && section !== 'remediation' && (
                  <Button
                    variant="subtle"
                    size="sm"
                    onClick={() => navigate('remediation', remediations.find(r=>r.status==='pending')?.incidentId)}
                    className="hidden sm:inline-flex"
                  >
                    {isEn
                      ? `${pendingRemediationsCount} Pending Remediation`
                      : `${pendingRemediationsCount} Remediation chờ xác nhận`}
                  </Button>
                )}

                {!live && demoMutated && (
                  <Button variant="secondary" size="sm" onClick={resetState}>
                    <RotateCcw className="h-3.5 w-3.5" />
                    {isEn ? 'Reset state' : 'Đặt lại'}
                  </Button>
                )}

                {live?.header}
                {/* Bilingual VI / EN Switcher */}
                <div
                  role="group"
                  aria-label={isEn ? 'Language switcher' : 'Chuyển ngôn ngữ'}
                  className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50 p-0.5"
                >
                  {(['vi', 'en'] as Locale[]).map((code) => {
                    const active = locale === code;
                    return (
                      <button
                        key={code}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setLocale(code)}
                        className={cn(
                          'rounded-md px-2.5 py-1 text-xs font-bold uppercase tracking-wide transition-colors cursor-pointer',
                          active
                            ? 'bg-orange-600 text-white shadow-2xs'
                            : 'text-slate-600 hover:text-slate-900',
                        )}
                      >
                        {code}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </header>

          {/* Workspace Body */}
          <main className="flex-1 mx-auto w-full max-w-[1440px] px-4 sm:px-6 lg:px-8 py-6 md:py-7">
            {section !== 'incident-detail' && (
              <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
                <div>
                  <h1 className="text-2xl md:text-[28px] font-bold tracking-tight text-slate-900">
                    {isEn ? currentMeta.pageTitleEn : currentMeta.pageTitleVi}
                  </h1>
                  <p className="mt-1 text-sm text-slate-500">
                    {isEn ? currentMeta.pageSubtitleEn : currentMeta.pageSubtitleVi}
                  </p>
                </div>
              </div>
            )}

            {live?.notice}
            {live?.connected && <div className="mb-4 flex flex-wrap gap-3"><Button variant="secondary" size="sm" onClick={live.onRefresh}>{isEn?'Refresh live data':'Làm mới dữ liệu'}</Button><a className="text-sm text-orange-700 underline self-center" href="/ui/demo/">{isEn?'Original simulation':'Bản mô phỏng gốc'}</a></div>}
            {!live?.connected && live ? live.gate : <>
            {live && !incidents.length && section!=='dashboard' ? <div className="rounded-xl border bg-white p-8 text-slate-500">{isEn?'No incidents in the loaded feed.':'Chưa có incident trong danh sách đã tải.'}</div> :
            <AnimatePresence mode="wait">
              <motion.div
                key={section === 'incident-detail' ? `detail-${currentIncident?.id || ''}` : section}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.16, ease: 'easeOut' }}
              >
                {section === 'dashboard' && (
                  <DashboardView
                    incidents={incidents}
                    remediations={remediations}
                    services={services}
                    onOpenIncident={openIncident}
                    onNavigate={navigate}
                    live={live ? {trend:live.trend} : undefined}
                  />
                )}

                {section === 'incidents' && (
                  <IncidentsView
                    incidents={incidents}
                    onOpenIncident={openIncident}
                  />
                )}

                {section === 'incident-detail' && (
                  <IncidentDetailView
                    incident={currentIncident}
                    remediation={currentIncidentRemediation}
                    onBack={() => navigate('incidents')}
                    onNavigate={navigate}
                  />
                )}

                {section === 'evidence' && (
                  <EvidenceTraceView
                    incidents={incidents}
                    selectedIncidentId={selectedIncidentId}
                    onSelectIncident={selectIncident}
                    services={services}
                    live={Boolean(live)}
                    onRecentTraces={live?.onRecentTraces}
                  />
                )}

                {section === 'rca' && (
                  <RcaView
                    incidents={incidents}
                    selectedIncidentId={selectedIncidentId}
                    onSelectIncident={selectIncident}
                    onNavigate={navigate}
                  />
                )}

                {section === 'remediation' && (
                  <RemediationView
                    remediations={remediations}
                    focusIncidentId={selectedIncidentId}
                    onApprove={handleApproveRemediation}
                    onReject={handleRejectRemediation}
                    onOpenIncident={openIncident}
                    live={Boolean(live)}
                    onSelectionChange={live?selectIncident:undefined}
                  />
                )}
                {section==='remediation' && live && <div className="mt-6 space-y-6">{live.reviewPanel}{live.journal}</div>}

                {section === 'reports' && (
                  <ReportsView
                    incidents={incidents}
                    remediations={remediations}
                    onOpenIncident={openIncident}
                    live={Boolean(live)}
                  />
                )}
              </motion.div>
            </AnimatePresence>}
            </>}
          </main>
        </div>
      </div>
    </MotionConfig>
  );
}
