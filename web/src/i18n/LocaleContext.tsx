import * as React from 'react';

export type Locale = 'vi' | 'en';

interface LocaleContextValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
  isEn: boolean;
}

const STORAGE_KEY = 'sentinelops.web.locale';

function readInitialLocale(): Locale {
  if (typeof window === 'undefined') return 'vi';
  const params = new URLSearchParams(window.location.search);
  const queryLang = params.get('lang')?.toLowerCase();
  if (queryLang === 'en' || queryLang === 'vi') return queryLang;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || saved === 'vi') return saved;
  } catch {
    // Ignore storage restriction
  }
  return 'vi';
}

const LocaleContext = React.createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = React.useState<Locale>(readInitialLocale);

  const setLocale = React.useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore storage restriction
    }
    const url = new URL(window.location.href);
    url.searchParams.set('lang', next);
    window.history.replaceState(null, '', url.pathname + url.search + url.hash);
  }, []);

  React.useEffect(() => {
    document.documentElement.lang = locale;
    document.title =
      locale === 'en'
        ? 'SentinelOps — AIOps & Incident Intelligence'
        : 'SentinelOps — Nền tảng AIOps & Incident Intelligence';
  }, [locale]);

  const value = React.useMemo(
    () => ({ locale, setLocale, isEn: locale === 'en' }),
    [locale, setLocale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const ctx = React.useContext(LocaleContext);
  if (!ctx) throw new Error('useLocale must be used inside LocaleProvider');
  return ctx;
}
