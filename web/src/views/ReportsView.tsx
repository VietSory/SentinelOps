import * as React from 'react';
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  BarChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge, SeverityBadge, StatusBadge } from '@/components/ui/badge';
import { getReportTrends } from '@/data';
import { formatDateTime, formatNumber } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type {
  EnvironmentName,
  Incident,
  RemediationProposal,
  ServiceName,
} from '@/types/sentinelops';

export interface ReportsViewProps {
  incidents: Incident[];
  remediations: RemediationProposal[];
  onOpenIncident: (incidentId: string) => void;
}

export function ReportsView({
  incidents,
  remediations,
  onOpenIncident,
}: ReportsViewProps) {
  const { locale, isEn } = useLocale();
  const [period, setPeriod] = React.useState<'daily' | 'weekly'>('daily');
  const [serviceFilter, setServiceFilter] = React.useState<'' | ServiceName>('');
  const [envFilter, setEnvFilter] = React.useState<'' | EnvironmentName>('');

  const scopedIncidents = React.useMemo(() => {
    return incidents.filter((inc) => {
      if (serviceFilter && !inc.services.includes(serviceFilter)) return false;
      if (envFilter && inc.environment !== envFilter) return false;
      return true;
    });
  }, [incidents, serviceFilter, envFilter]);

  const avg = (values: number[]) =>
    values.length
      ? values.reduce((sum, v) => sum + v, 0) / values.length
      : 0;

  const mttd = avg(scopedIncidents.map((i) => i.timingsMin.mttd));
  const mtti = avg(scopedIncidents.map((i) => i.timingsMin.mtti));
  const resolvedMttr = scopedIncidents
    .map((i) => i.timingsMin.mttr)
    .filter((v): v is number => v !== null);
  const mttr = avg(resolvedMttr);

  const scopedRemediations = remediations.filter(
    (r) => !serviceFilter || r.service === serviceFilter,
  );
  const completedRem = scopedRemediations.filter(
    (r) => r.status === 'success' || r.status === 'failed',
  );
  const successRem = completedRem.filter((r) => r.status === 'success').length;
  const successRate = completedRem.length
    ? Math.round((successRem / completedRem.length) * 100)
    : 100;

  const topServicesData = React.useMemo(() => {
    const counts: Record<ServiceName, number> = {
      Payment: 0,
      Checkout: 0,
      Inventory: 0,
    };
    for (const inc of scopedIncidents) {
      for (const svc of inc.services) {
        counts[svc] += 1;
      }
    }
    return (Object.entries(counts) as [ServiceName, number][])
      .map(([service, count]) => ({ service, count }))
      .sort((a, b) => b.count - a.count);
  }, [scopedIncidents]);

  const trends = getReportTrends(locale);
  const trendData = period === 'daily' ? trends.daily : trends.weekly;
  const minUnit = isEn ? 'min' : 'phút';
  const shortMin = isEn ? 'm' : 'p';

  return (
    <div className="space-y-6">
      <Card className="p-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">
                {isEn ? 'Granularity' : 'Chu kỳ xu hướng'}
              </span>
              <select
                value={period}
                onChange={(e) => setPeriod(e.target.value as 'daily' | 'weekly')}
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800"
              >
                <option value="daily">
                  {isEn ? 'Daily (Last 7 days)' : 'Theo ngày (7 ngày gần nhất)'}
                </option>
                <option value="weekly">
                  {isEn ? 'Weekly (Last 4 weeks)' : 'Theo tuần (4 tuần gần nhất)'}
                </option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">Service</span>
              <select
                value={serviceFilter}
                onChange={(e) => setServiceFilter(e.target.value as '' | ServiceName)}
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800"
              >
                <option value="">{isEn ? 'All Services' : 'Tất cả Service'}</option>
                <option value="Payment">Payment</option>
                <option value="Checkout">Checkout</option>
                <option value="Inventory">Inventory</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">Environment</span>
              <select
                value={envFilter}
                onChange={(e) => setEnvFilter(e.target.value as '' | EnvironmentName)}
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800"
              >
                <option value="">{isEn ? 'All Environments' : 'Tất cả môi trường'}</option>
                <option value="Production">Production</option>
                <option value="Staging">Staging</option>
              </select>
            </label>
          </div>

          <Badge tone="neutral">
            {isEn
              ? `${scopedIncidents.length} incidents in scope`
              : `Dữ liệu tổng hợp: ${scopedIncidents.length} sự cố`}
          </Badge>
        </div>
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Mean Time to Detect (MTTD)
          </p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-slate-900">
            {formatNumber(mttd, 1, locale)}{' '}
            <span className="text-sm font-medium text-slate-500">{minUnit}</span>
          </p>
          <p className="mt-1.5 text-xs text-emerald-700 font-medium">
            {isEn ? 'Automated SLO & ML detection' : 'Phát hiện tự động qua SLO bucket + ML'}
          </p>
        </Card>

        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Mean Time to Investigate (MTTI)
          </p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-orange-600">
            {formatNumber(mtti, 1, locale)}{' '}
            <span className="text-sm font-medium text-slate-500">{minUnit}</span>
          </p>
          <p className="mt-1.5 text-xs text-slate-500">
            {isEn ? '68% faster with automated RCA & Evidence' : 'Rút ngắn 68% nhờ Evidence & RCA tự động'}
          </p>
        </Card>

        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Mean Time to Resolve (MTTR)
          </p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-slate-900">
            {formatNumber(mttr, 1, locale)}{' '}
            <span className="text-sm font-medium text-slate-500">{minUnit}</span>
          </p>
          <p className="mt-1.5 text-xs text-slate-500">
            {isEn
              ? `Across ${resolvedMttr.length} verified resolved incidents`
              : `Tính trên ${resolvedMttr.length} sự cố đã đóng & xác minh`}
          </p>
        </Card>

        <Card accent="orange" className="p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Remediation Success Rate
          </p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-emerald-700">{successRate}%</p>
          <p className="mt-1.5 text-xs text-slate-600 tabular-nums">
            {isEn
              ? `${successRem}/${completedRem.length} executions passed SLO verification`
              : `${successRem}/${completedRem.length} lượt khắc phục vượt qua bước Verify SLO`}
          </p>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Card className="xl:col-span-7">
          <CardHeader>
            <div>
              <CardTitle>
                {period === 'daily'
                  ? isEn ? 'Daily Incident & MTTR Trend' : 'Xu hướng Incident & MTTR theo ngày'
                  : isEn ? 'Weekly Incident & MTTR Trend' : 'Xu hướng Incident & MTTR theo tuần'}
              </CardTitle>
              <CardDescription>
                {isEn
                  ? 'Total incidents (orange bars) vs. average MTTR (dark line)'
                  : 'So sánh số lượng sự cố phát sinh (cột cam) và thời gian xử lý trung bình MTTR (đường đậm)'}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-[260px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trendData} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#64748B' }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="left" allowDecimals={false} tick={{ fontSize: 12, fill: '#64748B' }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="right" orientation="right" unit={shortMin} tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      borderRadius: '0.75rem',
                      border: '1px solid #E2E8F0',
                      fontSize: '12px',
                    }}
                  />
                  <Bar
                    yAxisId="left"
                    dataKey="incidents"
                    name={isEn ? 'Total Incidents' : 'Tổng Incident'}
                    fill="#F97316"
                    radius={[6, 6, 0, 0]}
                    barSize={28}
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="mttrMin"
                    name={isEn ? 'MTTR (min)' : 'MTTR (phút)'}
                    stroke="#0F172A"
                    strokeWidth={2.2}
                    dot={{ r: 3.5, fill: '#EA580C', strokeWidth: 2, stroke: '#FFFFFF' }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="xl:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>
                {isEn ? 'Top Incident-Prone Services' : 'Top Services dễ gặp sự cố nhất'}
              </CardTitle>
              <CardDescription>
                {isEn
                  ? 'Frequency of involvement across filtered incidents'
                  : 'Số lần xuất hiện trong các sự cố đã ghi nhận theo bộ lọc'}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-[260px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={topServicesData}
                  layout="vertical"
                  margin={{ top: 8, right: 24, left: 10, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12, fill: '#64748B' }} axisLine={false} tickLine={false} />
                  <YAxis
                    type="category"
                    dataKey="service"
                    width={86}
                    tick={{ fontSize: 13, fontWeight: 600, fill: '#0F172A' }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      borderRadius: '0.75rem',
                      border: '1px solid #E2E8F0',
                      fontSize: '12px',
                    }}
                  />
                  <Bar
                    dataKey="count"
                    name={isEn ? 'Related Incidents' : 'Số sự cố liên quan'}
                    radius={[0, 8, 8, 0]}
                    barSize={28}
                  >
                    {topServicesData.map((entry, idx) => (
                      <Cell
                        key={entry.service}
                        fill={idx === 0 ? '#EA580C' : idx === 1 ? '#FB923C' : '#94A3B8'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{isEn ? 'Incident History' : 'Lịch sử vận hành & Incident'}</CardTitle>
            <CardDescription>
              {isEn
                ? 'All incidents in scope with detection, investigation, and resolution timings'
                : 'Toàn bộ sự cố trong phạm vi báo cáo cùng thời gian phát hiện, điều tra và khắc phục'}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div
            tabIndex={0}
            role="region"
            aria-label={isEn ? 'Incident history table' : 'Bảng lịch sử sự cố vận hành'}
            className="table-wrapper border border-slate-200"
          >
            <table className="ops-data-table responsive-stack text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th scope="col" className="px-4 py-3">Incident</th>
                  <th scope="col" className="px-4 py-3">Service</th>
                  <th scope="col" className="px-4 py-3">Severity</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">MTTD / MTTI / MTTR</th>
                  <th scope="col" className="px-4 py-3 text-right">{isEn ? 'Started' : 'Thời điểm'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {scopedIncidents.map((inc) => (
                  <tr
                    key={inc.id}
                    onClick={() => onOpenIncident(inc.id)}
                    className="cursor-pointer transition-colors hover:bg-orange-50/45"
                  >
                    <th scope="row" className="px-4 py-3 text-left font-normal">
                      <span className="font-mono text-xs font-semibold text-orange-700">
                        {inc.id}
                      </span>
                      <p className="font-semibold text-slate-900 line-clamp-1">{inc.title}</p>
                    </th>
                    <td data-label="Service" className="px-4 py-3 text-slate-700 font-medium">
                      {inc.services.join(', ')}
                    </td>
                    <td data-label="Severity" className="px-4 py-3">
                      <SeverityBadge severity={inc.severity} />
                    </td>
                    <td data-label="Status" className="px-4 py-3">
                      <StatusBadge status={inc.status} />
                    </td>
                    <td
                      data-label="MTTD / MTTI / MTTR"
                      className="px-4 py-3 text-right font-mono text-xs text-slate-700 tabular-nums"
                    >
                      {inc.timingsMin.mttd}{shortMin} · {inc.timingsMin.mtti}{shortMin} ·{' '}
                      {inc.timingsMin.mttr !== null
                        ? `${inc.timingsMin.mttr}${shortMin}`
                        : isEn ? 'Open' : 'Đang mở'}
                    </td>
                    <td
                      data-label={isEn ? 'Started' : 'Thời điểm'}
                      className="px-4 py-3 text-right text-xs text-slate-500 tabular-nums whitespace-nowrap"
                    >
                      {formatDateTime(inc.startedAt, locale)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
