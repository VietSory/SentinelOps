import * as React from 'react';
import { Search, RotateCcw, ArrowUpRight } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge, SeverityBadge, StatusBadge } from '@/components/ui/badge';
import { cn, formatDateTime, formatRelativeMinutes } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type {
  EnvironmentName,
  Incident,
  IncidentStatus,
  ServiceName,
  Severity,
} from '@/types/sentinelops';

export interface IncidentsViewProps {
  incidents: Incident[];
  onOpenIncident: (incidentId: string) => void;
}

type TimeRange = 'all' | '24h' | '7d' | '30d';
type RemediationFilter = 'all' | 'yes' | 'no';

export function IncidentsView({ incidents, onOpenIncident }: IncidentsViewProps) {
  const { locale, isEn } = useLocale();
  const [search, setSearch] = React.useState('');
  const [severity, setSeverity] = React.useState<'' | Severity>('');
  const [status, setStatus] = React.useState<'' | IncidentStatus>('');
  const [service, setService] = React.useState<'' | ServiceName>('');
  const [timeRange, setTimeRange] = React.useState<TimeRange>('all');
  const [environment, setEnvironment] = React.useState<'' | EnvironmentName>('');
  const [remediationFilter, setRemediationFilter] = React.useState<RemediationFilter>('all');

  const hasActiveFilters = Boolean(
    search.trim() ||
      severity ||
      status ||
      service ||
      timeRange !== 'all' ||
      environment ||
      remediationFilter !== 'all',
  );

  const resetFilters = () => {
    setSearch('');
    setSeverity('');
    setStatus('');
    setService('');
    setTimeRange('all');
    setEnvironment('');
    setRemediationFilter('all');
  };

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    const now = Date.now();
    const rangeMs: Record<TimeRange, number> = {
      all: Infinity,
      '24h': 24 * 3_600_000,
      '7d': 7 * 24 * 3_600_000,
      '30d': 30 * 24 * 3_600_000,
    };

    return incidents.filter((inc) => {
      if (q) {
        const hay = `${inc.id} ${inc.title} ${inc.suspectedRootCause} ${inc.shortSummaryVi} ${inc.services.join(' ')}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (severity && inc.severity !== severity) return false;
      if (status && inc.status !== status) return false;
      if (service && !inc.services.includes(service)) return false;
      if (environment && inc.environment !== environment) return false;
      if (remediationFilter === 'yes' && !inc.hasRemediation) return false;
      if (remediationFilter === 'no' && inc.hasRemediation) return false;
      if (timeRange !== 'all' && now - Date.parse(inc.startedAt) > rangeMs[timeRange]) {
        return false;
      }
      return true;
    });
  }, [incidents, search, severity, status, service, environment, remediationFilter, timeRange]);

  const selectClass =
    'h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 transition-colors hover:border-slate-300 focus:border-orange-500';

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h2 className="text-sm font-semibold text-slate-900">
            {isEn ? 'Incident Filters' : 'Bộ lọc Incident (Filter)'}
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-slate-600 tabular-nums">
              {isEn ? 'Showing ' : 'Hiển thị '}
              <strong className="text-orange-700">{filtered.length}</strong> / {incidents.length}{' '}
              {isEn ? 'incidents' : 'sự cố'}
            </span>
            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={resetFilters}>
                <RotateCcw className="h-3.5 w-3.5" />
                {isEn ? 'Clear filters' : 'Xóa bộ lọc'}
              </Button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
          <label className="xl:col-span-2 block">
            <span className="mb-1 block text-xs font-medium text-slate-600">
              {isEn ? 'Search' : 'Tìm kiếm'}
            </span>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={isEn ? 'Incident ID, service, root cause...' : 'Incident ID, service, root cause...'}
                className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 hover:border-slate-300"
              />
            </div>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600">Severity</span>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as '' | Severity)}
              className={selectClass}
            >
              <option value="">{isEn ? 'All severities' : 'Tất cả mức độ'}</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600">Status</span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as '' | IncidentStatus)}
              className={selectClass}
            >
              <option value="">{isEn ? 'All statuses' : 'Tất cả trạng thái'}</option>
              <option value="incident">{isEn ? 'Open' : 'Đang xử lý'}</option>
              <option value="degrading">{isEn ? 'Degrading' : 'Suy giảm'}</option>
              <option value="recovering">{isEn ? 'Recovering' : 'Đang phục hồi'}</option>
              <option value="recovered">{isEn ? 'Resolved' : 'Đã xử lý'}</option>
              <option value="escalated">{isEn ? 'Escalated' : 'Chuyển kỹ sư'}</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600">Service</span>
            <select
              value={service}
              onChange={(e) => setService(e.target.value as '' | ServiceName)}
              className={selectClass}
            >
              <option value="">{isEn ? 'All services' : 'Tất cả Service'}</option>
              <option value="Checkout">Checkout</option>
              <option value="Payment">Payment</option>
              <option value="Inventory">Inventory</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600">
              {isEn ? 'Time Range' : 'Khoảng thời gian'}
            </span>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value as TimeRange)}
              className={selectClass}
            >
              <option value="all">{isEn ? 'All time' : 'Toàn bộ'}</option>
              <option value="24h">{isEn ? 'Last 24 hours' : '24 giờ qua'}</option>
              <option value="7d">{isEn ? 'Last 7 days' : '7 ngày qua'}</option>
              <option value="30d">{isEn ? 'Last 30 days' : '30 ngày qua'}</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600">Environment</span>
            <select
              value={environment}
              onChange={(e) => setEnvironment(e.target.value as '' | EnvironmentName)}
              className={selectClass}
            >
              <option value="">{isEn ? 'All environments' : 'Tất cả môi trường'}</option>
              <option value="Production">Production</option>
              <option value="Staging">Staging</option>
            </select>
          </label>
        </div>

        <div className="mt-3.5 pt-3.5 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-slate-500">Remediation:</span>
            {(
              [
                ['all', isEn ? 'All' : 'Tất cả'],
                ['yes', isEn ? 'Has Remediation' : 'Có đề xuất Remediation'],
                ['no', isEn ? 'No Remediation' : 'Không có Remediation'],
              ] as const
            ).map(([val, label]) => (
              <button
                key={val}
                type="button"
                onClick={() => setRemediationFilter(val)}
                className={cn(
                  'rounded-full px-3 py-1 text-xs font-semibold transition-colors cursor-pointer border',
                  remediationFilter === val
                    ? 'border-orange-500 bg-orange-50 text-orange-700'
                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <Card>
        <CardContent className="p-0">
          {filtered.length === 0 ? (
            <div className="p-12 text-center">
              <p className="text-base font-semibold text-slate-900">
                {isEn ? 'No incidents match your filters' : 'Không tìm thấy sự cố phù hợp với bộ lọc'}
              </p>
              <p className="mt-1 text-sm text-slate-500">
                {isEn
                  ? 'Try broadening the time range or clearing active filters.'
                  : 'Thử nới lỏng khoảng thời gian hoặc xóa một số điều kiện lọc hiện tại.'}
              </p>
              <Button variant="secondary" className="mt-4" onClick={resetFilters}>
                {isEn ? 'Reset filters' : 'Đặt lại bộ lọc'}
              </Button>
            </div>
          ) : (
            <div
              tabIndex={0}
              role="region"
              aria-label={isEn ? 'Filtered incidents list' : 'Danh sách sự cố đã lọc'}
              className="table-wrapper"
            >
              <table className="ops-data-table responsive-stack text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <th scope="col" className="px-5 py-3.5">
                      {isEn ? 'Incident ID & Summary' : 'Incident ID & Tóm tắt'}
                    </th>
                    <th scope="col" className="px-4 py-3.5">Service</th>
                    <th scope="col" className="px-4 py-3.5">Severity / Status</th>
                    <th scope="col" className="px-4 py-3.5">
                      {isEn ? 'Suspected Root Cause' : 'Root Cause nghi ngờ'}
                    </th>
                    <th scope="col" className="px-4 py-3.5">{isEn ? 'Started' : 'Bắt đầu'}</th>
                    <th scope="col" className="px-4 py-3.5 text-right">{isEn ? 'Detail' : 'Chi tiết'}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {filtered.map((inc) => {
                    const isCriticalOpen = inc.severity === 'critical' && inc.status !== 'recovered';
                    return (
                      <tr
                        key={inc.id}
                        onClick={() => onOpenIncident(inc.id)}
                        className={cn(
                          'group cursor-pointer transition-colors hover:bg-orange-50/50',
                          isCriticalOpen && 'bg-red-50/45',
                        )}
                      >
                        <th scope="row" className="px-5 py-4 text-left font-normal max-w-md">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs font-bold text-orange-700">
                              {inc.id}
                            </span>
                            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">
                              {inc.environment}
                            </span>
                            {inc.hasRemediation && (
                              <Badge tone="high" className="text-[10px] px-2 py-0">
                                {isEn ? 'Remediation' : 'Có Remediation'}
                              </Badge>
                            )}
                            {inc.rcaAbstained && (
                              <Badge tone="neutral" className="text-[10px] px-2 py-0">
                                RCA Abstain
                              </Badge>
                            )}
                          </div>
                          <p className="mt-1 font-semibold text-slate-900 group-hover:text-orange-700 transition-colors">
                            {inc.title}
                          </p>
                          <p className="mt-0.5 text-xs text-slate-500 line-clamp-2">
                            {inc.shortSummaryVi}
                          </p>
                        </th>

                        <td data-label="Service" className="px-4 py-4 align-top">
                          <div className="flex flex-wrap gap-1.5">
                            {inc.services.map((svc) => (
                              <span
                                key={svc}
                                className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs font-semibold text-slate-700"
                              >
                                {svc}
                              </span>
                            ))}
                          </div>
                        </td>

                        <td data-label="Severity / Status" className="px-4 py-4 align-top">
                          <div className="flex flex-col items-start gap-1.5">
                            <SeverityBadge severity={inc.severity} />
                            <StatusBadge status={inc.status} />
                          </div>
                        </td>

                        <td data-label="Root Cause" className="px-4 py-4 align-top max-w-xs">
                          <p className="text-xs font-medium text-slate-800 leading-relaxed">
                            {inc.suspectedRootCause}
                          </p>
                          <div className="mt-1.5 flex items-center gap-2">
                            <div className="h-1.5 w-20 rounded-full bg-slate-100 overflow-hidden">
                              <div
                                className={cn(
                                  'h-full rounded-full',
                                  inc.rcaAbstained ? 'bg-slate-400' : 'bg-orange-500',
                                )}
                                style={{ width: `${inc.rcaConfidence}%` }}
                              />
                            </div>
                            <span className="text-[11px] font-semibold tabular-nums text-slate-500">
                              {inc.rcaAbstained ? 'Abstain' : `${inc.rcaConfidence}% confidence`}
                            </span>
                          </div>
                        </td>

                        <td
                          data-label={isEn ? 'Started' : 'Bắt đầu'}
                          className="px-4 py-4 align-top text-xs text-slate-500 tabular-nums whitespace-nowrap"
                        >
                          <div className="font-semibold text-slate-700">
                            {formatRelativeMinutes(inc.startedAt, locale)}
                          </div>
                          <div>{formatDateTime(inc.startedAt, locale)}</div>
                        </td>

                        <td data-label={isEn ? 'Detail' : 'Chi tiết'} className="px-4 py-4 align-top text-right">
                          <Button
                            variant="subtle"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenIncident(inc.id);
                            }}
                          >
                            {isEn ? 'Open' : 'Mở'}
                            <ArrowUpRight className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
