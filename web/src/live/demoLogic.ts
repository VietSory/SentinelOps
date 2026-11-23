// Pure demo UI logic (DEMO_UPGRADE_SPEC WP4). No imports: node --test loads this file directly.

type Row = any;
type Locale = 'vi' | 'en';

const ms = (value: unknown) => (typeof value === 'string' ? Date.parse(value) : NaN);
const HASH = /^[a-f0-9]{64}$/;

/** v2 proposals wait for a human decision; v1 keeps its original countdown. */
export function proposalTiming(row: Row, now: number) {
  const p = row?.proposal ?? {};
  const waiting = p.proposal_version === 'runbook-proposal/v2';
  const expiry = ms(p.expires_at);
  const remaining = waiting ? null : Number.isFinite(expiry) ? Math.max(0, Math.ceil((expiry - now) / 1000)) : 0;
  const current = row?.current_revision === undefined || row.current_revision === p.incident_revision;
  const reviewable = row?.status === 'pending' && current && typeof row?.proposal_sha256 === 'string' &&
    HASH.test(row.proposal_sha256) && now >= ms(p.created_at) && (waiting || (remaining ?? 0) > 0);
  return {waiting, remaining, reviewable, waitingSince: waiting ? p.created_at ?? null : null};
}

const STATUS: Record<string, [string, string]> = {
  pending: ['Chờ duyệt', 'Waiting for approval'],
  approved: ['Đã duyệt', 'Approved'],
  consumed: ['Đang thực thi', 'Executing'],
  obsolete: ['Đã đóng: tình trạng đã thay đổi', 'Closed: situation changed'],
  engineer: ['Chuyển kỹ sư', 'Escalated to an engineer'],
  expired: ['Hết hạn', 'Expired'],
  stale: ['Đã có revision mới', 'Superseded by a newer revision'],
  recovered: ['Đã hồi phục', 'Recovered'],
  escalated: ['Chuyển kỹ sư', 'Escalated to an engineer'],
  rejected: ['Đã từ chối', 'Rejected'],
  blocked: ['Bị chặn', 'Blocked'],
  verifying: ['Đang xác minh hồi phục', 'Verifying recovery'],
  dispatching: ['Đang thực thi', 'Executing'],
};

export function proposalStatusLabel(status: string, locale: Locale) {
  const value = STATUS[status];
  return value ? value[locale === 'en' ? 1 : 0] : status;
}

export type RiskCard = {service: string; state: string; score: number | null; startedAt: string | null;
  lastScoredAt: string | null; thresholds: {watch: number | null; degrading: number | null}; muted: boolean};

/** Cards only for services at watch/degrading; unavailable forecasts are shown muted. */
export function riskCards(body: Row): RiskCard[] {
  if (!body || body.schema_version !== 'live-risk/v1' || typeof body.services !== 'object' || !body.services) return [];
  const order = ['degrading', 'watch', 'unavailable'];
  const cards: RiskCard[] = [];
  for (const [service, value] of Object.entries<Row>(body.services)) {
    if (!value || !order.includes(value.state)) continue;
    cards.push({service, state: value.state, score: typeof value.score === 'number' ? value.score : null,
      startedAt: value.episode_active ? value.episode_started_at ?? null : null,
      lastScoredAt: value.last_scored_at ?? null,
      thresholds: {watch: value.thresholds?.watch ?? null, degrading: value.thresholds?.degrading ?? null},
      muted: value.state === 'unavailable'});
  }
  return cards.sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state));
}

const MAX_LEAD_SECONDS = 30 * 60;

/** Seconds between the last warning start before onset and the onset (within 30 minutes), else null. */
export function warningLead(episodes: string[], firstOnsetAt: string | null) {
  const onset = ms(firstOnsetAt);
  if (!Number.isFinite(onset) || !Array.isArray(episodes)) return null;
  const before = episodes.map(ms).filter(t => Number.isFinite(t) && t <= onset && onset - t <= MAX_LEAD_SECONDS * 1000);
  if (!before.length) return null;
  return Math.round((onset - Math.max(...before)) / 1000);
}

const STEPS = ['warning', 'breach', 'incident', 'approved', 'recovered'] as const;

export function incidentTimeline(parts: Partial<Record<typeof STEPS[number], string | null>>) {
  const steps: {key: string; at: string; secondsFromPrevious: number | null}[] = [];
  let previous: number | null = null;
  for (const key of STEPS) {
    const at = parts[key];
    const time = ms(at);
    if (!at || !Number.isFinite(time)) continue;
    steps.push({key, at, secondsFromPrevious: previous === null ? null : Math.round((time - previous) / 1000)});
    previous = time;
  }
  return steps;
}

/** Evidence scores are weighted sums on 0-1, not calibrated probabilities: never show a percent. */
export function scoreLabel(kind: 'rca' | 'runbook', value: number | null, locale: Locale) {
  const name = kind === 'rca' ? (locale === 'en' ? 'RCA score' : 'Điểm RCA')
    : (locale === 'en' ? 'Runbook match' : 'Mức khớp runbook');
  if (typeof value !== 'number' || !Number.isFinite(value)) return `${name} —`;
  const shown = value.toFixed(2);
  return `${name} ${locale === 'en' ? shown : shown.replace('.', ',')}`;
}

/** A 0-100 scaled score shown as a 0-1 number in the reader's locale ("0,82" / "0.82"). */
export function scoreValue(scaled: number | null | undefined, locale: Locale) {
  if (typeof scaled !== 'number' || !Number.isFinite(scaled)) return '—';
  const shown = (scaled / 100).toFixed(2);
  return locale === 'en' ? shown : shown.replace('.', ',');
}
