import * as React from 'react';
import { cn } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type { IncidentStatus, RemediationStatus, Severity } from '@/types/sentinelops';

type BadgeTone = 'critical' | 'high' | 'medium' | 'low' | 'success' | 'neutral' | 'orange';

const toneClasses: Record<BadgeTone, string> = {
  critical: 'bg-red-50 text-red-700 border-red-200',
  high: 'bg-orange-50 text-orange-700 border-orange-200',
  medium: 'bg-amber-50 text-amber-800 border-amber-200',
  low: 'bg-slate-100 text-slate-700 border-slate-200',
  success: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  neutral: 'bg-slate-100 text-slate-600 border-slate-200',
  orange: 'bg-orange-600 text-white border-orange-600',
};

const dotClasses: Record<BadgeTone, string> = {
  critical: 'bg-red-600',
  high: 'bg-orange-500',
  medium: 'bg-amber-500',
  low: 'bg-slate-400',
  success: 'bg-emerald-500',
  neutral: 'bg-slate-400',
  orange: 'bg-white',
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  dot?: boolean;
  pulse?: boolean;
}

export function Badge({
  tone = 'neutral',
  dot = false,
  pulse = false,
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold leading-5 whitespace-nowrap',
        toneClasses[tone],
        className,
      )}
      {...props}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={cn(
            'h-1.5 w-1.5 rounded-full shrink-0',
            dotClasses[tone],
            pulse && 'status-pulse text-red-500',
          )}
        />
      )}
      {children}
    </span>
  );
}

const SEVERITY_META: Record<Severity, { label: string; tone: BadgeTone }> = {
  critical: { label: 'Critical', tone: 'critical' },
  high: { label: 'High', tone: 'high' },
  medium: { label: 'Medium', tone: 'medium' },
  low: { label: 'Low', tone: 'low' },
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const meta = SEVERITY_META[severity];
  return (
    <Badge tone={meta.tone} dot pulse={severity === 'critical'}>
      {meta.label}
    </Badge>
  );
}

const STATUS_META: Record<IncidentStatus, { vi: string; en: string; tone: BadgeTone }> = {
  incident: { vi: 'Đang xử lý', en: 'Open', tone: 'critical' },
  degrading: { vi: 'Suy giảm', en: 'Degrading', tone: 'high' },
  recovering: { vi: 'Đang phục hồi', en: 'Recovering', tone: 'medium' },
  recovered: { vi: 'Đã xử lý', en: 'Resolved', tone: 'success' },
  escalated: { vi: 'Chuyển kỹ sư', en: 'Escalated', tone: 'critical' },
};

export function StatusBadge({ status }: { status: IncidentStatus }) {
  const { isEn } = useLocale();
  const meta = STATUS_META[status];
  return (
    <Badge tone={meta.tone} dot>
      {isEn ? meta.en : meta.vi}
    </Badge>
  );
}

const REMEDIATION_META: Record<RemediationStatus, { vi: string; en: string; tone: BadgeTone }> = {
  expired: {vi:'Hết hạn',en:'Expired',tone:'neutral'},
  stale: {vi:'Revision cũ',en:'Stale revision',tone:'neutral'},
  engineer: {vi:'Chuyển kỹ sư',en:'Engineer review',tone:'critical'},
  consumed: {vi:'Quyết định đã được nhận',en:'Decision consumed',tone:'medium'},
  not_yet_valid: {vi:'Chưa tới hạn',en:'Not yet valid',tone:'neutral'},
  pending: { vi: 'Pending · Chờ xác nhận', en: 'Pending Approval', tone: 'high' },
  approved: { vi: 'Approved · Đã duyệt', en: 'Approved', tone: 'medium' },
  running: { vi: 'Running · Đang thực thi', en: 'Running', tone: 'orange' },
  success: { vi: 'Success · Đã xác minh', en: 'Verified · Success', tone: 'success' },
  failed: { vi: 'Failed · Thất bại', en: 'Verification Failed', tone: 'critical' },
  rejected: { vi: 'Rejected · Đã từ chối', en: 'Rejected', tone: 'neutral' },
  dispatching: { vi: 'Đang gửi thực thi', en: 'Dispatching', tone: 'orange' },
  verifying: { vi: 'Đang xác minh hồi phục', en: 'Verifying recovery', tone: 'medium' },
  recovered: { vi: 'Đã xác minh hồi phục', en: 'Verified recovery', tone: 'success' },
  blocked: { vi: 'Bị chặn', en: 'Blocked', tone: 'critical' },
  escalated: { vi: 'Chuyển kỹ sư', en: 'Escalated', tone: 'critical' },
};

export function RemediationBadge({ status }: { status: RemediationStatus }) {
  const { isEn } = useLocale();
  const meta = Object.prototype.hasOwnProperty.call(REMEDIATION_META, status)
    ? REMEDIATION_META[status]
    : { vi: 'Chưa rõ', en: 'Unknown', tone: 'neutral' as const };
  return (
    <Badge tone={meta.tone} dot>
      {isEn ? meta.en : meta.vi}
    </Badge>
  );
}

export function Keyword({ children }: { children: React.ReactNode }) {
  return (
    <strong className="font-semibold text-orange-700 bg-orange-50/90 px-1.5 py-0.5 rounded">
      {children}
    </strong>
  );
}
