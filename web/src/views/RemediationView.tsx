import * as React from 'react';
import { motion } from 'framer-motion';
import {
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  ShieldCheck,
  AlertTriangle,
  ArrowUpRight,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, Keyword, RemediationBadge } from '@/components/ui/badge';
import { NativeDialog } from '@/components/ui/dialog';
import { INCIDENTS_PER_PAGE, Pagination } from '@/components/ui/pagination';
import { cn, formatDateTime } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type { RemediationProposal, RemediationStatus } from '@/types/sentinelops';
import { scoreValue } from '@/live/demoLogic';

export interface RemediationViewProps {
  live?: boolean;
  onSelectionChange?: (id:string)=>void;
  remediations: RemediationProposal[];
  focusIncidentId?: string;
  onApprove: (proposalId: string, reason: string) => void;
  onReject: (proposalId: string, reason: string) => void;
  onOpenIncident: (incidentId: string) => void;
}

type StatusFilter = 'all' | 'pending' | 'active' | 'success' | 'failed';

export function RemediationView({
  live, onSelectionChange,
  remediations,
  focusIncidentId,
  onApprove,
  onReject,
  onOpenIncident,
}: RemediationViewProps) {
  const { locale, isEn } = useLocale();
  const [statusFilter, setStatusFilter] = React.useState<StatusFilter>('all');
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [historyPage, setHistoryPage] = React.useState(1);

  React.useEffect(() => {
    setHistoryPage(1);
  }, [statusFilter]);

  const defaultReasonVi =
    'Đã kiểm tra Trace bottleneck Payment → Payment DB và Deployment Receipt v2.8.4. Đồng ý rollback về bản Known-Good v2.8.3.';
  const defaultReasonEn =
    'Verified Payment → Payment DB bottleneck trace and v2.8.4 deployment receipt. Approved rollback to Known-Good v2.8.3.';

  const [decisionReason, setDecisionReason] = React.useState(
    isEn ? defaultReasonEn : defaultReasonVi,
  );
  const [confirmMode, setConfirmMode] = React.useState<'approve' | 'reject' | null>(null);

  React.useEffect(() => {
    setDecisionReason((prev) => {
      if (prev === defaultReasonVi || prev === defaultReasonEn) {
        return isEn ? defaultReasonEn : defaultReasonVi;
      }
      return prev;
    });
  }, [isEn]);

  const selectedProposal = remediations.find((proposal) => proposal.id === selectedId);
  const activeProposal = selectedProposal
    ?? remediations.find((proposal) => proposal.incidentId === focusIncidentId)
    ?? remediations[0];
  const fallbackIncidentId = selectedProposal ? undefined : activeProposal?.incidentId;
  const hasMissingSelection = selectedId !== null && !selectedProposal;
  const reportFallback = React.useEffectEvent((incidentId: string) => {
    onSelectionChange?.(incidentId);
  });
  React.useEffect(() => {
    if (hasMissingSelection) setSelectedId(null);
  }, [hasMissingSelection]);
  React.useEffect(() => {
    if (live && fallbackIncidentId && fallbackIncidentId !== focusIncidentId) {
      reportFallback(fallbackIncidentId);
    }
  }, [live, fallbackIncidentId, focusIncidentId]);

  const selectProposal = (proposal: RemediationProposal) => {
    setSelectedId(proposal.id);
    if (live) onSelectionChange?.(proposal.incidentId);
  };

  const filteredHistory = React.useMemo(() => {
    return remediations.filter((r) => {
      if (statusFilter === 'all') return true;
      if (statusFilter === 'pending') return r.status === 'pending';
      if (statusFilter === 'active') return r.status === 'approved' || r.status === 'running';
      if (statusFilter === 'success') return r.status === 'success';
      if (statusFilter === 'failed') return r.status === 'failed' || r.status === 'rejected';
      return true;
    });
  }, [remediations, statusFilter]);

  const totalHistoryPages = Math.max(1, Math.ceil(filteredHistory.length / INCIDENTS_PER_PAGE));
  const safeHistoryPage = Math.min(historyPage, totalHistoryPages);
  React.useEffect(() => {
    if (historyPage > totalHistoryPages) setHistoryPage(totalHistoryPages);
  }, [historyPage, totalHistoryPages]);
  const pagedHistory = React.useMemo(() => {
    const start = (safeHistoryPage - 1) * INCIDENTS_PER_PAGE;
    return filteredHistory.slice(start, start + INCIDENTS_PER_PAGE);
  }, [filteredHistory, safeHistoryPage]);

  const pendingCount = remediations.filter((r) => r.status === 'pending').length;
  const successCount = remediations.filter((r) => r.status === 'success').length;
  const failedCount = remediations.filter((r) => r.status === 'failed' || r.status === 'engineer').length;

  const handleConfirm = () => {
    if (!activeProposal || !confirmMode) return;
    const reason =
      decisionReason.trim() ||
      (isEn ? 'Confirmed by operator in SentinelOps.' : 'Đã xác nhận bởi người vận hành trên SentinelOps.');
    if (confirmMode === 'approve') {
      onApprove(activeProposal.id, reason);
    } else {
      onReject(activeProposal.id, reason);
    }
    setConfirmMode(null);
  };

  const riskBadge = (risk: RemediationProposal['riskLevel']) => {
    if (risk === 'unknown') return <Badge tone="neutral">{isEn?'Risk not provided':'Chưa có mức rủi ro'}</Badge>;
    if (risk === 'high') return <Badge tone="critical">{isEn ? 'High Risk' : 'Rủi ro cao'}</Badge>;
    if (risk === 'medium') return <Badge tone="high">{isEn ? 'Medium Risk' : 'Rủi ro trung bình'}</Badge>;
    return <Badge tone="success">{isEn ? 'Low Risk' : 'Rủi ro thấp'}</Badge>;
  };

  const stepIcon = (st: 'done' | 'running' | 'pending' | 'failed') => {
    if (st === 'done') return <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />;
    if (st === 'failed') return <XCircle className="h-4 w-4 text-red-600 shrink-0" />;
    if (st === 'running') return <Play className="h-4 w-4 text-orange-600 shrink-0" />;
    return <Clock className="h-4 w-4 text-slate-400 shrink-0" />;
  };

  const statusToneAccent = (st: RemediationStatus) => {
    if (st === 'pending' || st === 'running') return 'orange' as const;
    if (st === 'failed') return 'red' as const;
    if (st === 'success') return 'emerald' as const;
    return 'none' as const;
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card accent={pendingCount > 0 ? 'orange' : 'none'} className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isEn ? 'Pending Approval' : 'Đang chờ xác nhận (Pending)'}
          </p>
          <p className="mt-1.5 text-3xl font-bold tabular-nums text-orange-600">{pendingCount}</p>
          <p className="mt-1 text-xs text-slate-500">
            {isEn
              ? 'Requires human-in-the-loop approval before execution'
              : 'Yêu cầu con người xác nhận (Human-in-the-loop) trước khi thực thi'}
          </p>
        </Card>

        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isEn ? 'Verified Success' : 'Đã khắc phục & Verify thành công'}
          </p>
          <p className="mt-1.5 text-3xl font-bold tabular-nums text-emerald-700">{successCount}</p>
          <p className="mt-1 text-xs text-slate-500">
            {isEn ? 'SLO restored: Latency < 750 ms, Error Rate < 5%' : 'Đạt chuẩn SLO Latency < 750 ms và Error Rate < 5%'}
          </p>
        </Card>

        <Card accent={failedCount > 0 ? 'red' : 'none'} className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {isEn ? 'Verification Failed / Escalated' : 'Verify thất bại / Chuyển kỹ sư'}
          </p>
          <p className="mt-1.5 text-3xl font-bold tabular-nums text-red-700">{failedCount}</p>
          <p className="mt-1 text-xs text-slate-500">
            {isEn
              ? 'Halted automatically when post-check verification fails'
              : 'Tự động dừng an toàn khi kiểm chứng hậu khắc phục không đạt'}
          </p>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        <Card className="min-w-0 xl:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>
                {isEn ? 'Proposals & Action History' : 'Danh sách Đề xuất & Lịch sử Action'}
              </CardTitle>
              <CardDescription>
                {isEn
                  ? 'Select an action to inspect runbook steps, approve, or check verification'
                  : 'Chọn một hành động để xem runbook, phê duyệt hoặc đối chiếu kết quả verify'}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ['all', isEn ? 'All' : 'Tất cả'],
                  ['pending', 'Pending'],
                  ['active', isEn ? 'Running' : 'Đang chạy'],
                  ['success', 'Success'],
                  ['failed', 'Failed'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setStatusFilter(key)}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-xs font-semibold cursor-pointer transition-colors',
                    statusFilter === key
                      ? 'border-orange-500 bg-orange-50 text-orange-700'
                      : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="space-y-2.5">
              {pagedHistory.map((item) => {
                const selected = activeProposal?.id === item.id;
                const isFailed = item.status === 'failed';
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => selectProposal(item)}
                    className={cn(
                      'w-full min-w-0 text-left rounded-xl border p-3.5 transition-all cursor-pointer space-y-2',
                      selected
                        ? 'border-orange-500 bg-orange-50/55 ring-1 ring-orange-200'
                        : isFailed
                          ? 'border-red-200 bg-red-50/35 hover:border-red-300'
                          : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/70',
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="min-w-0 break-all font-mono text-xs font-bold text-slate-800">
                        {item.id} · {item.service}
                      </span>
                      <RemediationBadge status={item.status} />
                    </div>
                    <p className="text-sm font-semibold text-slate-900 line-clamp-2 break-words [overflow-wrap:anywhere]">
                      {item.runbookName}
                    </p>
                    <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-[11px] text-slate-500 tabular-nums">
                      <span className="min-w-0 break-all font-mono">{item.incidentId}</span>
                      <span className="shrink-0">{formatDateTime(item.createdAt, locale)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
            <Pagination
              currentPage={safeHistoryPage}
              totalItems={filteredHistory.length}
              pageSize={INCIDENTS_PER_PAGE}
              onPageChange={setHistoryPage}
              itemLabelVi="đề xuất"
              itemLabelEn="proposals"
              className="px-0 pb-0"
            />
          </CardContent>
        </Card>

        {activeProposal && (
          <motion.div
            key={activeProposal.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className="min-w-0 xl:col-span-7 space-y-5"
          >
            <Card accent={statusToneAccent(activeProposal.status)}>
              <CardHeader>
                <div className="min-w-0 max-w-full space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 break-all font-mono text-xs font-bold text-orange-700 bg-white border border-orange-200 px-2 py-0.5 rounded">
                      {activeProposal.id}
                    </span>
                    <RemediationBadge status={activeProposal.status} />
                    {riskBadge(activeProposal.riskLevel)}
                  </div>
                  <CardTitle className="text-lg pt-1">{activeProposal.runbookName}</CardTitle>
                  <CardDescription>
                    Runbook: <code className="font-mono text-slate-700 break-all">{activeProposal.runbookId}</code> ·{' '}
                    {live?(isEn?'Runbook match: ':'Điểm khớp runbook: '):(isEn ? 'RCA Confidence: ' : 'Độ tin cậy RCA: ')}
                    <strong className="text-orange-700">{Number.isFinite(activeProposal.confidence)?scoreValue(activeProposal.confidence,isEn?'en':'vi'):(isEn?'Not provided':'Chưa có điểm')}</strong>
                  </CardDescription>
                </div>

                <Button
                  variant="secondary"
                  size="sm"
                  title={activeProposal.incidentId}
                  className="max-w-full min-w-0"
                  onClick={() => onOpenIncident(activeProposal.incidentId)}
                >
                  <span className="truncate">{activeProposal.incidentId}</span>
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
                </Button>
              </CardHeader>

              <CardContent className="space-y-5">
                <p className="text-sm text-slate-700 leading-relaxed break-words [overflow-wrap:anywhere]">
                  {activeProposal.summaryVi}
                </p>

                {activeProposal.currentImage && activeProposal.rollbackImage && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-white p-4">
                    <div className="min-w-0">
                      <span className="text-[11px] font-semibold uppercase tracking-wide text-red-600">
                        {isEn ? 'Current Image (Faulty)' : 'Image hiện tại (Gây lỗi)'}
                      </span>
                      <code className="mt-1 block rounded-lg bg-red-50 border border-red-200/80 px-2.5 py-2 font-mono text-[11px] text-red-900 break-all">
                        {activeProposal.currentImage}
                      </code>
                    </div>
                    <div className="min-w-0">
                      <span className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
                        {isEn ? 'Target Image (Known-Good)' : 'Image mục tiêu (Known-Good)'}
                      </span>
                      <code className="mt-1 block rounded-lg bg-emerald-50 border border-emerald-200/80 px-2.5 py-2 font-mono text-[11px] text-emerald-900 break-all">
                        {activeProposal.rollbackImage}
                      </code>
                    </div>
                  </div>
                )}

                <div className="space-y-2.5">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {isEn
                      ? `Runbook Steps (${activeProposal.steps.length})`
                      : `Các bước trong Runbook (${activeProposal.steps.length} bước)`}
                  </h4>
                  <ol className="space-y-2">
                    {activeProposal.steps.map((step) => (
                      <li
                        key={step.order}
                        className={cn(
                          'rounded-xl border p-3 flex items-start gap-3',
                          step.status === 'running'
                            ? 'border-orange-300 bg-orange-50/70'
                            : step.status === 'failed'
                              ? 'border-red-200 bg-red-50/60'
                              : step.status === 'done'
                                ? 'border-emerald-200/80 bg-emerald-50/35'
                                : 'border-slate-200 bg-white',
                        )}
                      >
                        <div className="mt-0.5">{stepIcon(step.status)}</div>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-sm font-semibold text-slate-900 break-words [overflow-wrap:anywhere]">
                              {step.order}. {step.title}
                            </span>
                            {step.durationSec !== undefined && (
                              <span className="font-mono text-[11px] text-slate-500">
                                {step.durationSec}s
                              </span>
                            )}
                          </div>
                          <code className="mt-1 block font-mono text-[11px] text-slate-500 break-all">
                            $ {step.command}
                          </code>
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>

                {activeProposal.status === 'pending' && !live && (
                  <div className="rounded-xl border-2 border-orange-300 bg-orange-50/70 p-4 space-y-3">
                    <div className="flex items-start gap-2.5">
                      <ShieldCheck className="h-5 w-5 text-orange-600 shrink-0 mt-0.5" />
                      <div>
                        <h4 className="text-sm font-bold text-slate-900">
                          {isEn ? 'On-call Confirmation & Approval' : 'Khu vực xác nhận & Phê duyệt (On-call Approval)'}
                        </h4>
                        <p className="text-xs text-slate-600 mt-0.5">
                          {isEn ? (
                            <>
                              <Keyword>Remediation</Keyword> executes only upon explicit approval and automatically runs SLO verification afterwards.
                            </>
                          ) : (
                            <>
                              Hệ thống chỉ thực thi <Keyword>Remediation</Keyword> sau khi có quyết định phê duyệt và tự động verify SLO ngay sau rollout.
                            </>
                          )}
                        </p>
                      </div>
                    </div>

                    <label className="block">
                      <span className="block text-xs font-semibold text-slate-700 mb-1">
                        {isEn ? 'Decision Note (stored in Audit Receipt)' : 'Ghi chú quyết định (lưu vào Audit Receipt)'}
                      </span>
                      <textarea
                        rows={2}
                        value={decisionReason}
                        onChange={(e) => setDecisionReason(e.target.value)}
                        className="w-full rounded-lg border border-orange-200 bg-white p-2.5 text-sm text-slate-900 placeholder:text-slate-400"
                      />
                    </label>

                    <div className="flex flex-wrap items-center gap-2.5 pt-1">
                      <Button
                        variant="primary"
                        size="lg"
                        onClick={() => setConfirmMode('approve')}
                      >
                        {isEn ? 'Approve & Execute Rollback' : 'Phê duyệt & Thực thi Rollback'}
                      </Button>
                      <Button
                        variant="danger"
                        size="lg"
                        onClick={() => setConfirmMode('reject')}
                      >
                        {isEn ? 'Reject & Escalate' : 'Từ chối & Chuyển kỹ sư'}
                      </Button>
                      <Button
                        variant="secondary"
                        size="lg"
                        onClick={() => onOpenIncident(activeProposal.incidentId)}
                      >
                        {isEn ? 'Review Incident' : 'Kiểm tra lại Incident'}
                      </Button>
                    </div>
                  </div>
                )}

                {activeProposal.status === 'running' && (
                  <div className="rounded-xl border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950 font-medium">
                    {isEn
                      ? 'Executing Runbook & monitoring rollout progress — SLO verification results will update automatically...'
                      : 'Đang thực thi Runbook & theo dõi tiến trình rollout — kết quả Verify SLO sẽ cập nhật tự động...'}
                  </div>
                )}

                {activeProposal.approvedBy && (
                  <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3.5 text-xs text-slate-600 space-y-1 break-words [overflow-wrap:anywhere]">
                    <div>
                      {isEn ? 'Decision by ' : 'Quyết định bởi '}
                      <strong className="text-slate-900">{activeProposal.approvedBy}</strong>{' '}
                      {activeProposal.approvedAt &&
                        (isEn
                          ? `at ${formatDateTime(activeProposal.approvedAt, locale)}`
                          : `lúc ${formatDateTime(activeProposal.approvedAt, locale)}`)}
                    </div>
                    {activeProposal.decisionReason && (
                      <p className="text-slate-700">
                        {isEn ? 'Reason: ' : 'Lý do: '}“{activeProposal.decisionReason}”
                      </p>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card
              accent={
                activeProposal.status === 'success'
                  ? 'emerald'
                  : activeProposal.status === 'failed'
                    ? 'red'
                    : 'none'
              }
            >
              <CardHeader>
                <div>
                  <CardTitle>
                    {isEn ? 'Post-Remediation Verification' : 'Kết quả Verify sau Remediation'}
                  </CardTitle>
                  <CardDescription>
                    {isEn
                      ? 'Before vs. after SLO health comparison'
                      : 'So sánh trực tiếp chỉ số trước và sau khi thực thi hành động khắc phục'}
                  </CardDescription>
                </div>
                {activeProposal.status === 'success' && (
                  <Badge tone="success" dot>
                    {isEn ? 'SLO Verified · Healthy' : 'SLO Verified · An toàn'}
                  </Badge>
                )}
                {activeProposal.status === 'failed' && (
                  <Badge tone="critical" dot>
                    {isEn ? 'Verification Failed · Halted' : 'Verify Thất bại · Đã dừng tự động'}
                  </Badge>
                )}
              </CardHeader>
              <CardContent className="space-y-4">
                {activeProposal.verificationSummaryVi && (
                  <div
                    className={cn(
                      'rounded-xl border p-3.5 text-sm flex items-start gap-2.5',
                      activeProposal.status === 'failed'
                        ? 'border-red-200 bg-red-50/70 text-red-900'
                        : 'border-emerald-200 bg-emerald-50/60 text-emerald-950',
                    )}
                  >
                    {activeProposal.status === 'failed' ? (
                      <AlertTriangle className="h-4 w-4 text-red-600 shrink-0 mt-0.5" />
                    ) : (
                      <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0 mt-0.5" />
                    )}
                    <span className="min-w-0 break-words [overflow-wrap:anywhere]">{activeProposal.verificationSummaryVi}</span>
                  </div>
                )}

                <div
                  tabIndex={0}
                  role="region"
                  aria-label={isEn ? 'Post-remediation verification table' : 'Bảng kiểm chứng chỉ số sau Remediation'}
                  className="table-wrapper border border-slate-200"
                >
                  <table className="ops-data-table text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                        <th scope="col" className="px-4 py-2.5">{isEn ? 'Metric' : 'Chỉ số kiểm chứng'}</th>
                        <th scope="col" className="px-4 py-2.5">{isEn ? 'Before' : 'Trước xử lý'}</th>
                        <th scope="col" className="px-4 py-2.5">{isEn ? 'After' : 'Sau xử lý'}</th>
                        <th scope="col" className="px-4 py-2.5">{isEn ? 'SLO Target' : 'Mục tiêu SLO'}</th>
                        <th scope="col" className="px-4 py-2.5 text-right">{isEn ? 'Result' : 'Kết quả'}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 tabular-nums">
                      {activeProposal.verificationChecks.map((chk) => (
                        <tr key={chk.metric}>
                          <th scope="row" className="px-4 py-3 text-left font-medium text-slate-900">
                            {chk.metric}
                          </th>
                          <td className="px-4 py-3 text-red-700 font-semibold">{chk.before}</td>
                          <td className="px-4 py-3 font-semibold text-slate-900">
                            {activeProposal.status === 'pending'
                              ? (isEn ? 'Expected: ' : 'Dự kiến: ') + chk.after
                              : chk.after}
                          </td>
                          <td className="px-4 py-3 text-slate-500">{chk.target}</td>
                          <td className="px-4 py-3 text-right">
                            {activeProposal.status === 'pending' || activeProposal.status === 'running' ? (
                              <Badge tone="neutral">{isEn ? 'Pending' : 'Chờ đo'}</Badge>
                            ) : chk.passed ? (
                              <Badge tone="success">{isEn ? 'Passed' : 'Đạt'}</Badge>
                            ) : (
                              <Badge tone="critical">{isEn ? 'Failed' : 'Không đạt'}</Badge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        )}
      </div>

      {!live && <NativeDialog
        open={confirmMode !== null}
        onClose={() => setConfirmMode(null)}
        title={
          confirmMode === 'approve'
            ? isEn
              ? 'Confirm Remediation Approval & Execution?'
              : 'Xác nhận phê duyệt & thực thi Remediation?'
            : isEn
              ? 'Confirm Rejection & Engineer Escalation?'
              : 'Xác nhận từ chối đề xuất & chuyển kỹ sư?'
        }
        description={
          activeProposal
            ? `${activeProposal.runbookName} (${activeProposal.incidentId})`
            : undefined
        }
      >
        <div className="space-y-4">
          <div
            className={cn(
              'rounded-xl border p-3.5 text-sm leading-relaxed',
              confirmMode === 'approve'
                ? 'border-orange-200 bg-orange-50/70 text-slate-800'
                : 'border-red-200 bg-red-50/70 text-red-900',
            )}
          >
            {confirmMode === 'approve' ? (
              isEn ? (
                <>
                  This will trigger a rollback of <strong>{activeProposal?.service}</strong> to the Known-Good image and automatically verify SLO recovery over 2 consecutive windows.
                </>
              ) : (
                <>
                  Hành động sẽ kích hoạt rollback Deployment <strong>{activeProposal?.service}</strong> về bản
                  Known-Good và tự động kiểm chứng SLO trong 2 cửa sổ đo liên tiếp.
                </>
              )
            ) : isEn ? (
              <>
                Automated execution will be skipped and this proposal will be escalated to an engineer for manual handling.
              </>
            ) : (
              <>
                Đề xuất tự động sẽ bị hủy thực thi và chuyển sang hàng đợi xử lý thủ công cho kỹ sư phụ trách.
              </>
            )}
          </div>

          <div className="flex flex-wrap justify-end gap-2.5 pt-2">
            <Button variant="secondary" autoFocus onClick={() => setConfirmMode(null)}>
              {isEn ? 'Cancel' : 'Hủy bỏ'}
            </Button>
            <Button
              variant={confirmMode === 'approve' ? 'primary' : 'danger'}
              onClick={handleConfirm}
            >
              {confirmMode === 'approve'
                ? isEn ? 'Confirm & Execute Now' : 'Xác nhận & Thực thi ngay'
                : isEn ? 'Confirm Rejection' : 'Xác nhận từ chối'}
            </Button>
          </div>
        </div>
      </NativeDialog>}
    </div>
  );
}
