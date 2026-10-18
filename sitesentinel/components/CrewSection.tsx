'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  ChevronDown,
  CloudUpload,
  LoaderCircle,
  MessageSquareText,
  Phone,
  Trash2,
  UserPlus,
  UserRound,
  Users,
} from 'lucide-react';
import { addCrew, listLocalCrew, onCrewChange, removeCrew, retryCrew, type LocalCrewMember } from '@/lib/crewStore';
import { formatPhone } from '@/lib/phone';
import type { CrewMember } from '@/lib/types';

const ROLES = ['Supervisor', 'Crew lead', 'Lineworker', 'Equipment operator', 'Laborer'];

type Row = {
  key: string;
  name?: string;
  role?: string;
  phone: string;
  serverId?: number;
  clientId?: string;
  state: 'saved' | 'pending' | 'failed';
  error?: string;
};

// "Assigned crew" for a site, with a Manage crew panel to add and remove crew members.
// Additions are saved on this device and on the server (lib/crewStore.ts); without signal they
// wait on the device and sync automatically.
export default function CrewSection({ siteId, siteName, crew }: { siteId: string; siteName: string; crew: CrewMember[] }) {
  const [local, setLocal] = useState<LocalCrewMember[]>([]);
  const [managing, setManaging] = useState(false);

  const refreshLocal = useCallback(() => {
    listLocalCrew(siteId).then(setLocal).catch(() => setLocal([]));
  }, [siteId]);
  useEffect(() => {
    refreshLocal();
    return onCrewChange(refreshLocal);
  }, [refreshLocal]);

  // The server's roster, plus anything added on this device that hasn't reached it yet.
  const onServer = new Set(crew.map((c) => c.phone));
  const rows: Row[] = [
    ...crew.map((c): Row => ({ key: `s${c.id}`, ...c, serverId: c.id, state: 'saved' })),
    ...local
      .filter((m) => m.status !== 'synced' && !onServer.has(m.phone))
      .map((m): Row => ({
        key: m.clientId,
        name: m.name,
        role: m.role,
        phone: m.phone,
        clientId: m.clientId,
        state: m.status === 'failed' ? 'failed' : 'pending',
        error: m.lastError,
      })),
  ];

  return (
    <section className="rounded-xl bg-white p-4" aria-labelledby="assigned-crew">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="assigned-crew" className="flex items-center gap-2 text-xl font-bold">
          <Users size={22} aria-hidden />
          Assigned crew
          <span className="rounded-full bg-[#16213E] px-2 text-sm font-bold text-white">{rows.length}</span>
        </h2>
        <div className="flex flex-wrap gap-2">
          <ForceSmsButton siteId={siteId} siteName={siteName} crewCount={crew.length} />
          <button
            type="button"
            onClick={() => setManaging((v) => !v)}
            aria-expanded={managing}
            aria-controls="manage-crew"
            className="flex min-h-11 items-center gap-1 rounded-lg border-2 border-[#16213E] px-3 font-bold text-[#16213E] hover:bg-slate-100"
          >
            Manage crew
            <ChevronDown size={18} aria-hidden className={`transition-transform ${managing ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {managing && <ManageCrewPanel siteId={siteId} />}

      {rows.length === 0 ? (
        <p className="text-slate-800">No crew assigned to this site yet.</p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {rows.map((r) => (
            <CrewRow key={r.key} row={r} siteId={siteId} managing={managing} />
          ))}
        </ul>
      )}
    </section>
  );
}

function CrewRow({ row, siteId, managing }: { row: Row; siteId: string; managing: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    if (!window.confirm(`Remove ${row.name ?? formatPhone(row.phone)} from this site's crew?`)) return;
    setBusy(true);
    setError(null);
    try {
      await removeCrew(siteId, { serverId: row.serverId, clientId: row.clientId, phone: row.phone });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-2 rounded-lg border-2 border-slate-200 p-3">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[#16213E]">
          <UserRound size={24} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-bold text-slate-900">{row.name ?? 'Crew member'}</div>
          {row.role && <div className="text-sm font-semibold text-slate-800">{row.role}</div>}
        </div>
        <a
          href={`tel:${row.phone}`}
          className="flex min-h-11 shrink-0 items-center gap-1 rounded-lg bg-[#16213E] px-3 font-semibold text-white hover:bg-[#0F172A]"
          aria-label={`Call ${row.name ?? 'crew member'} at ${formatPhone(row.phone)}`}
        >
          <Phone size={18} aria-hidden />
          {formatPhone(row.phone)}
        </a>
      </div>
      {row.state === 'pending' && (
        <p className="flex items-center gap-1 rounded bg-slate-800 px-2 py-0.5 text-sm font-semibold text-white">
          <CloudUpload size={16} aria-hidden /> Saved on this device; syncs when there&apos;s signal
        </p>
      )}
      {row.state === 'failed' && (
        <p className="flex flex-wrap items-center gap-2 rounded bg-[#C92A2A] px-2 py-0.5 text-sm font-semibold text-white">
          Not saved: {row.error}
          <button type="button" onClick={() => row.clientId && void retryCrew(row.clientId)} className="min-h-11 underline">
            Retry
          </button>
        </p>
      )}
      {(managing || row.state === 'failed') && (
        <button
          type="button"
          onClick={remove}
          disabled={busy}
          className="flex min-h-11 items-center justify-center gap-1 rounded-lg border-2 border-[#C92A2A] font-bold text-[#C92A2A] hover:bg-red-50 disabled:opacity-60"
        >
          {busy ? <LoaderCircle size={18} className="animate-spin" aria-hidden /> : <Trash2 size={18} aria-hidden />}
          Remove from site
        </button>
      )}
      {error && <p role="alert" className="text-sm font-bold text-[#C92A2A]">{error}</p>}
    </li>
  );
}

function ManageCrewPanel({ siteId }: { siteId: string }) {
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [role, setRole] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone: 'ok' | 'info' | 'error' } | null>(null);

  async function onAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setMessage({ text: 'Enter the crew member’s name.', tone: 'error' });
    setSaving(true);
    setMessage(null);
    try {
      const result = await addCrew(siteId, { name, phone, role });
      if (result.status === 'rejected') {
        setMessage({ text: result.error, tone: 'error' });
      } else {
        setMessage(
          result.status === 'synced'
            ? { text: `${name.trim()} added to the crew.`, tone: 'ok' }
            : { text: `${name.trim()} saved on this device. They'll be added when there's signal.`, tone: 'info' },
        );
        setName('');
        setPhone('');
        setRole('');
      }
    } catch {
      setMessage({ text: 'Could not save. Try again.', tone: 'error' });
    } finally {
      setSaving(false);
    }
  }

  const tone = { ok: 'bg-[#2F9E44] text-white', info: 'bg-slate-800 text-white', error: 'bg-[#C92A2A] text-white' };

  return (
    <div id="manage-crew" className="mb-4 flex flex-col gap-3 rounded-lg border-2 border-slate-300 bg-slate-50 p-3">
      <form onSubmit={onAdd} className="flex flex-col gap-3" noValidate>
        <h3 className="flex items-center gap-2 text-lg font-bold">
          <UserPlus size={20} aria-hidden /> Add a crew member
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 font-semibold">
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              autoComplete="off"
              className="min-h-12 rounded-lg border-2 border-slate-500 bg-white px-3 text-base font-normal"
            />
          </label>
          <label className="flex flex-col gap-1 font-semibold">
            Phone number
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              type="tel"
              inputMode="tel"
              autoComplete="off"
              placeholder="(205) 555-0123"
              className="min-h-12 rounded-lg border-2 border-slate-500 bg-white px-3 text-base font-normal"
            />
          </label>
          <label className="flex flex-col gap-1 font-semibold sm:col-span-2">
            Role (optional)
            <input
              value={role}
              onChange={(e) => setRole(e.target.value)}
              list="crew-roles"
              maxLength={100}
              className="min-h-12 rounded-lg border-2 border-slate-500 bg-white px-3 text-base font-normal"
            />
            <datalist id="crew-roles">
              {ROLES.map((r) => (
                <option key={r} value={r} />
              ))}
            </datalist>
          </label>
        </div>
        {message && (
          <p role={message.tone === 'error' ? 'alert' : 'status'} className={`rounded-lg p-2 font-bold ${tone[message.tone]}`}>
            {message.text}
          </p>
        )}
        <button
          type="submit"
          disabled={saving}
          className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-[#E8590C] text-lg font-extrabold text-white disabled:opacity-60"
        >
          {saving && <LoaderCircle className="animate-spin" aria-hidden />}
          {saving ? 'Saving…' : 'Add to crew'}
        </button>
      </form>
      <p className="text-sm text-slate-800">
        To remove someone, use <b>Remove from site</b> on their card below. Crew members get flood warning texts for
        this site.
      </p>
    </div>
  );
}

type SmsResult = { to: string; name: string | null; status: 'sent' | 'failed' | 'skipped'; error: string | null };

// Texts every crew member assigned to the site right now (through the backend's Twilio sender).
//
// TODO: this should send a proper status message for the site (active alerts, flood risk,
// current conditions, tomorrow's hazard score). For now it sends no message, so the backend
// uses a generic status line, and while the team is on Twilio's free trial every text goes out
// as Twilio's predefined template ("sms_internal_alerts", set by TWILIO_BODY_TEMPLATE) anyway.
function ForceSmsButton({ siteId, siteName, crewCount }: { siteId: string; siteName: string; crewCount: number }) {
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ text: string; ok: boolean } | null>(null);

  async function send() {
    const who = `${crewCount} crew member${crewCount === 1 ? '' : 's'}`;
    if (!window.confirm(`Text an SMS update to ${who} at ${siteName}?`)) return;
    setSending(true);
    setResult(null);
    try {
      const res = await fetch(`/api/sites/${encodeURIComponent(siteId)}/sms`, { method: 'POST' });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.error ?? `Could not send (HTTP ${res.status})`);
      const list = json as SmsResult[];
      const sent = list.filter((r) => r.status === 'sent').length;
      const problem = list.find((r) => r.status !== 'sent')?.error;
      setResult({ text: `Sent to ${sent} of ${list.length}${problem ? ` (${problem})` : ''}`, ok: sent === list.length });
    } catch (err) {
      setResult({
        text: err instanceof TypeError ? 'No signal: not sent' : err instanceof Error ? err.message : 'Could not send',
        ok: false,
      });
    } finally {
      setSending(false);
    }
  }

  return (
    <span className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={send}
        disabled={sending || crewCount === 0}
        title={crewCount === 0 ? 'No crew assigned yet' : undefined}
        className="flex min-h-11 items-center gap-1 rounded-lg bg-[#16213E] px-3 font-bold text-white hover:bg-[#0F172A] disabled:opacity-60"
      >
        {sending ? <LoaderCircle size={18} className="animate-spin" aria-hidden /> : <MessageSquareText size={18} aria-hidden />}
        Force SMS Update
      </button>
      {result && (
        <span role="status" className={`text-sm font-semibold ${result.ok ? 'text-[#2F9E44]' : 'text-[#C92A2A]'}`}>
          {result.text}
        </span>
      )}
    </span>
  );
}
