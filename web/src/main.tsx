import { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { LocaleProvider } from '@/i18n/LocaleContext';
import './index.css';

const App = lazy(() => window.location.pathname.startsWith('/ui/demo/')
  ? import('./App') : import('./live/LiveApp'));
createRoot(document.getElementById('root')!).render(
  <LocaleProvider><Suspense fallback={<p role="status">SentinelOps · Loading…</p>}><App /></Suspense></LocaleProvider>,
);
