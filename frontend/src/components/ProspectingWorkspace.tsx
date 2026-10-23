'use client';

import { useEffect, useRef, useState } from 'react';
import { request } from '../lib/api';
import './prospecting-workspace.css';

type Draft = { subject: string; body: string; status: 'local_draft'; recipient: null; review_required: boolean; source_urls: string[] };
type Candidate = { id: string; name: string; source_url: string; retrieved_at: string; observed: { description: string; source: string; organization_status: string }; inferred: { fit_score: number; matched_terms: string[]; reason: string }; contact: { status: string; email: null }; draft: Draft | null };
type Run = { id: string; market: string; product: string; limit: number; status: string; results: Candidate[]; events: { type: string; at: string; code?: string }[]; gmail: { status: string; supported_draft?: boolean; detail?: string }; limitations: string[] };
type Profile = { url: string; title: string; description: string; market: string; product: string; assumptions: string[]; retrieved_at: string };
const terminal = (status: string) => ['completed', 'partial', 'failed', 'cancelled'].includes(status);
const STATUS_LABELS: Record<string, string> = { pending: 'Queued', running: 'Researching public sources', completed: 'Research complete', partial: 'Partial results', failed: 'Research stopped', cancel_requested: 'Stopping…', cancelled: 'Cancelled' };
const statusLabel = (status: string) => STATUS_LABELS[status] || status;
const message = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

export default function ProspectingWorkspace() {
  const [url, setUrl] = useState('');
  const [profile, setProfile] = useState<Profile | null>(null);
  const [manual, setManual] = useState(false);
  const [market, setMarket] = useState('');
  const [product, setProduct] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [selected, setSelected] = useState('');
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [retry, setRetry] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const operation = useRef<AbortController | null>(null);
  const active = !!run && !terminal(run.status);
  const candidate = run?.results.find(item => item.id === selected) || run?.results[0];
  const runId = run?.id;
  useEffect(() => {
    const controller = new AbortController();
    request('/prospecting/runs', { signal: controller.signal }).then((rows: Run[]) => {
      setRuns(rows); setRun(current => current || rows[0] || null);
    }).catch(error => { if (!controller.signal.aborted) setError(message(error)); });
    return () => { controller.abort(); operation.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setPollError('');
    async function poll() {
      try {
        const next: Run = await request(`/prospecting/runs/${encodeURIComponent(runId!)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setRun(next); setRuns(rows => [next, ...rows.filter(row => row.id !== next.id)]);
        if (!terminal(next.status)) timer = setTimeout(poll, 2500);
      } catch (error) { if (!controller.signal.aborted) setPollError(message(error)); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, retry]);
  useEffect(() => { setDraft(candidate?.draft || null); setNotice(''); }, [candidate?.id, runId]);

  async function perform(kind: string, action: (signal: AbortSignal) => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setPending(kind); setError(''); setNotice('');
    const controller = new AbortController(); operation.current = controller;
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted) setError(message(error)); }
    finally { if (!controller.signal.aborted) setPending(''); inFlight.current = false; }
  }
  function understand(event: React.FormEvent) {
    event.preventDefault();
    void perform('profile', async signal => {
      const next: Profile = await request('/prospecting/profile', { method: 'POST', signal, body: JSON.stringify({ url: url.trim() }) }, 25000);
      if (signal.aborted) return;
      setProfile(next); setMarket(next.market || ''); setProduct(next.product || ''); setConfirmed(false); setManual(true);
    });
  }
  function start(event: React.FormEvent) {
    event.preventDefault(); if (active || !confirmed || market.trim().length < 2 || product.trim().length < 2) return;
    void perform('run', async signal => {
      const next: Run = await request('/prospecting/runs', { method: 'POST', signal, body: JSON.stringify({ market: market.trim(), product: product.trim(), limit: 20 }) });
      if (signal.aborted) return;
      setRun(next); setSelected(''); setDraft(null); setRuns(rows => [next, ...rows.filter(row => row.id !== next.id)]);
    });
  }
  function prepareDraft() {
    if (!run || !candidate || !terminal(run.status)) return;
    const currentRun = run, currentCandidate = candidate;
    void perform('draft', async signal => {
      const next: Draft = await request(`/prospecting/runs/${encodeURIComponent(currentRun.id)}/results/${encodeURIComponent(currentCandidate.id)}/draft`, { method: 'POST', signal, body: JSON.stringify({ sender_name: 'Your team' }) });
      if (signal.aborted) return;
      setRun(current => current?.id === currentRun.id ? { ...current, results: current.results.map(item => item.id === currentCandidate.id ? { ...item, draft: next } : item) } : current);
      setDraft(next); setNotice('Draft saved in this app. Nothing was created in Gmail or sent.');
    });
  }
  return <main className="prospecting-app">
    <header className="prospecting-top"><a href="/" className="prospecting-brand">Open GrokBot <span>/ Prospecting</span></a><a href="/">Back to workspace ↗</a></header>
    <div className="prospecting-layout"><aside className="prospecting-setup">
      <span className="prospecting-eyebrow">FROM WEBSITE TO SHORTLIST</span><h1>Find companies worth a closer look.</h1><p className="prospecting-intro">Start with your company website. Review the offer, research public company candidates, then prepare an introduction.</p>
      <form onSubmit={understand} className="prospecting-form"><label htmlFor="prospecting-url">1. Your company website</label><input id="prospecting-url" type="url" required maxLength={2048} placeholder="https://yourcompany.com" value={url} onChange={event => { setUrl(event.target.value); setProfile(null); setConfirmed(false); }} disabled={!!pending}/><button className="prospecting-primary" disabled={!!pending}>{pending === 'profile' ? 'Reading public website…' : 'Understand company'}</button><small>Reads public website copy. No model calls or private account access.</small></form>
      <button className="prospecting-text-button" type="button" onClick={() => setManual(value => !value)} aria-expanded={manual}>{manual ? 'Hide offer details' : 'Or enter your offer manually'}</button>
      {manual && <form onSubmit={start} className="prospecting-form prospecting-confirm"><h2>2. Confirm your offer</h2>{profile && <div className="prospecting-profile"><strong>{profile.title || 'Website summary'}</strong><a href={profile.url} target="_blank" rel="noopener noreferrer">View source ↗</a>{profile.assumptions.map((item, index) => <p key={index}>{item}</p>)}</div>}<label htmlFor="prospecting-market">Who could this help?</label><input id="prospecting-market" value={market} maxLength={300} minLength={2} required placeholder="e.g. Independent retail teams" onChange={event => { setMarket(event.target.value); setConfirmed(false); }} disabled={!!pending}/><label htmlFor="prospecting-product">What do you offer?</label><textarea id="prospecting-product" value={product} maxLength={2000} minLength={2} required rows={5} placeholder="Describe your product and the problem it solves." onChange={event => { setProduct(event.target.value); setConfirmed(false); }} disabled={!!pending}/><label className="prospecting-checkbox"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={!!pending}/>I reviewed this offer and target market.</label><button className="prospecting-primary" disabled={!!pending || !confirmed || active}>{pending === 'run' ? 'Starting research…' : 'Find up to 20 company candidates'}</button><small>Wikipedia candidates; inferred relevance, not verified customers or buying intent.</small></form>}
      {error && <div role="alert" className="prospecting-error">{error} <a href="/login">Sign in</a></div>}
      <div className="prospecting-history"><h2>Email connections</h2><p>Gmail API: connection required. The saved website browser plugin cannot create API drafts.</p><p>Resend: provider setup required. No per-user outreach connection is verified.</p><a href="/?plugins=1">Manage existing plugins →</a><p>App drafts and Gmail compose are available below. Neither means an email was sent.</p></div>
      <div className="prospecting-history"><h2>Recent research</h2>{!runs.length && <p>Your research will appear here.</p>}{runs.map(row => <button type="button" key={row.id} className={runId === row.id ? 'is-selected' : ''} disabled={!!pending} onClick={() => { setRun(row); setSelected(''); setDraft(null); }} aria-pressed={runId === row.id}><strong>{row.market}</strong><span>{statusLabel(row.status)} · {row.results.length} candidates</span></button>)}</div>
    </aside><section className="prospecting-content" aria-label="Research results">
      {!run ? <div className="prospecting-empty"><span aria-hidden="true">◎</span><h2>A useful shortlist starts with context.</h2><p>Enter your website on the left. You can edit every assumption before research begins.</p><div><strong>01</strong> Understand your company <strong>02</strong> Review candidates <strong>03</strong> Prepare an app draft</div></div> : <>
        <div className="prospecting-results-heading"><div><span className="prospecting-eyebrow">PUBLIC SOURCE RESEARCH</span><h2>{run.market}</h2><p role="status">{statusLabel(run.status)} · {run.results.length} of up to {run.limit} candidates</p></div>{active && <button type="button" disabled={!!pending || run.status === 'cancel_requested'} onClick={() => void perform('cancel', async signal => { const next = await request(`/prospecting/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST', signal }); if (!signal.aborted) { setRun(next); setRetry(value => value + 1); } })}>{pending === 'cancel' || run.status === 'cancel_requested' ? 'Stopping…' : 'Stop research'}</button>}</div>
        {pollError && <div className="prospecting-error" role="alert">{pollError} <button type="button" onClick={() => setRetry(value => value + 1)}>Reconnect</button></div>}
        <div className="prospecting-limitations"><strong>Company candidates, not verified leads.</strong> Scores measure word overlap with your offer. Contact details, demand and suitability need review.</div>
        {!run.results.length && <div className="prospecting-empty compact"><h3>{active ? 'Checking public company descriptions…' : 'No company candidates available.'}</h3><p>{active ? 'Results appear as research progresses. You can leave this screen and return to the saved run.' : 'Try a more specific market or adjust the offer. Source coverage is limited.'}</p></div>}
        {!!run.results.length && <div className="prospecting-results-grid"><div className="prospecting-candidates" aria-label="Company candidates">{run.results.map(item => <button type="button" key={item.id} disabled={!!pending} className={candidate?.id === item.id ? 'is-selected' : ''} onClick={() => { setSelected(item.id); setDraft(item.draft); setNotice(''); }} aria-pressed={candidate?.id === item.id}><span className="prospecting-company-initial" aria-hidden="true">{item.name.charAt(0)}</span><span><strong>{item.name}</strong><small>{item.observed.source} · Company candidate</small></span><span className="prospecting-fit">{item.inferred.fit_score}<small>relevance</small></span></button>)}</div>
          {candidate && <article className="prospecting-detail"><span className="prospecting-eyebrow">REVIEW CANDIDATE</span><h2>{candidate.name}</h2><a href={candidate.source_url} target="_blank" rel="noopener noreferrer">Read public source ↗</a><p>Retrieved <time dateTime={candidate.retrieved_at}>{new Date(candidate.retrieved_at).toLocaleString()}</time></p><h3>What the source says</h3><p className="prospecting-description">{candidate.observed.description}</p><h3>Why it appeared</h3><p>{candidate.inferred.reason}</p><div className="prospecting-terms">{candidate.inferred.matched_terms.map(term => <span key={term}>{term}</span>)}</div><p className="prospecting-contact">Contact needed · No verified email address</p><button type="button" className="prospecting-primary" disabled={!!pending || active} onClick={prepareDraft}>{pending === 'draft' ? 'Saving app draft…' : draft ? 'Regenerate app draft' : 'Prepare app draft'}</button>{active && <small>Draft preparation becomes available when research finishes.</small>}
          {draft && <div className="prospecting-draft"><h3>Draft stored in this app</h3><p>Review every detail and add a verified recipient. Edits below stay on this screen.</p><label htmlFor="prospecting-subject">Subject</label><input id="prospecting-subject" value={draft.subject} onChange={event => setDraft({ ...draft, subject: event.target.value })}/><label htmlFor="prospecting-body">Message</label><textarea id="prospecting-body" rows={9} value={draft.body} onChange={event => setDraft({ ...draft, body: event.target.value })}/><a className="prospecting-compose" href={`https://mail.google.com/mail/?view=cm&fs=1&su=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`} target="_blank" rel="noopener noreferrer">Open Gmail compose ↗</a><p>Opens a compose screen. This app has not saved a Gmail draft or sent an email.</p><button type="button" disabled>Sending disabled — review required</button></div>}
          {notice && <p className="prospecting-notice" role="status">{notice}</p>}<div className="prospecting-gmail"><strong>Gmail capability</strong><p>{run.gmail?.detail || 'No verified Gmail draft API is available. Drafts stay in the app database.'}</p><a href="/?plugins=1">Manage connections in workspace →</a></div></article>}
        </div>}
        <details className="prospecting-activity"><summary>Research activity · {run.events.length} updates</summary><ol>{run.events.map((event, index) => <li key={`${event.at}-${index}`}><span>{event.type.replace(/_/g, ' ')}{event.code ? ` · ${event.code.replace(/_/g, ' ')}` : ''}</span><time dateTime={event.at}>{new Date(event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></li>)}</ol></details>
      </>}
    </section></div>
  </main>;
}
