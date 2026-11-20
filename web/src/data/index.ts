import type { Locale } from '@/i18n/LocaleContext';
import {
  DASHBOARD_TREND_24H,
  INITIAL_INCIDENTS,
  INITIAL_REMEDIATIONS,
  INITIAL_SERVICES,
  REPORT_DAILY_TREND,
  REPORT_WEEKLY_TREND,
} from './mockData';
import {
  DASHBOARD_TREND_24H_EN,
  INITIAL_INCIDENTS_EN,
  INITIAL_REMEDIATIONS_EN,
  INITIAL_SERVICES_EN,
  REPORT_DAILY_TREND_EN,
  REPORT_WEEKLY_TREND_EN,
} from './mockDataEn';

export function getInitialDataset(locale: Locale) {
  if (locale === 'en') {
    return {
      incidents: INITIAL_INCIDENTS_EN,
      remediations: INITIAL_REMEDIATIONS_EN,
      services: INITIAL_SERVICES_EN,
    };
  }
  return {
    incidents: INITIAL_INCIDENTS,
    remediations: INITIAL_REMEDIATIONS,
    services: INITIAL_SERVICES,
  };
}

export function getDashboardTrend(locale: Locale) {
  return locale === 'en' ? DASHBOARD_TREND_24H_EN : DASHBOARD_TREND_24H;
}

export function getReportTrends(locale: Locale) {
  return locale === 'en'
    ? { daily: REPORT_DAILY_TREND_EN, weekly: REPORT_WEEKLY_TREND_EN }
    : { daily: REPORT_DAILY_TREND, weekly: REPORT_WEEKLY_TREND };
}
