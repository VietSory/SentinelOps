import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Locale } from '@/i18n/LocaleContext';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(
  value: number,
  maximumFractionDigits = 1,
  locale: Locale = 'vi',
): string {
  if (!Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(locale === 'en' ? 'en-US' : 'vi-VN', {
    maximumFractionDigits,
  }).format(value);
}

/** All operator-facing times are Vietnam time, whatever the viewer's browser zone is. */
export const VIETNAM_TIME_ZONE = 'Asia/Ho_Chi_Minh';

export function formatDateTime(iso: string, locale: Locale = 'vi'): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return locale === 'en' ? 'Unknown' : 'Chưa rõ';
  return `${new Intl.DateTimeFormat(locale === 'en' ? 'en-US' : 'vi-VN', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: VIETNAM_TIME_ZONE,
  }).format(date)} (GMT+7)`;
}

/** Full Vietnam-time stamp with seconds, for audit fields such as deadlines and generation times. */
export function formatVietnamStamp(value: string | number | Date, locale: Locale = 'vi'): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'vi-VN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: VIETNAM_TIME_ZONE,
  }).format(date)} (GMT+7)`;
}

export function formatVietnamClock(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('vi-VN', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: VIETNAM_TIME_ZONE,
  }).format(date);
}

export function formatRelativeMinutes(iso: string, locale: Locale = 'vi'): string {
  if (!Number.isFinite(Date.parse(iso))) return locale === 'en' ? 'Unknown' : 'Chưa rõ';
  const diffMin = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (locale === 'en') {
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const hours = Math.floor(diffMin / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }
  if (diffMin < 1) return 'Vừa xong';
  if (diffMin < 60) return `${diffMin} phút trước`;
  const hours = Math.floor(diffMin / 60);
  if (hours < 24) return `${hours} giờ trước`;
  const days = Math.floor(hours / 24);
  return `${days} ngày trước`;
}
