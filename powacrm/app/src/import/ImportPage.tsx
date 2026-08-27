import { useState } from 'react';
import Papa from 'papaparse';
import { useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { useBrand } from '@/shell/BrandContext';
import { applyMapping, guessMapping, type TargetField } from './mapColumns';

const FIELDS: TargetField[] = ['first_name', 'last_name', 'email', 'title', 'linkedin_url', 'company_name', 'company_domain'];

type ImportResult = { inserted: number; restored: number; skipped: number; errors: { row: number; message: string }[] };
type ImportOutcome = { ok: ImportResult } | { error: string };

export function ImportPage() {
  const { brand } = useBrand();
  const qc = useQueryClient();
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, string>[]>([]);
  const [filename, setFilename] = useState('');
  const [mapping, setMapping] = useState<Record<TargetField, string | null> | null>(null);
  const [result, setResult] = useState<ImportOutcome | null>(null);
  const [busy, setBusy] = useState(false);

  function onFile(f: File) {
    setFilename(f.name); setResult(null);
    Papa.parse<Record<string, string>>(f, {
      header: true, skipEmptyLines: true,
      complete: res => {
        const hs = res.meta.fields ?? [];
        setHeaders(hs); setRows(res.data); setMapping(guessMapping(hs));
      },
    });
  }

  async function runImport() {
    if (!mapping) return;
    setBusy(true);
    try {
      const { data: batch, error: bErr } = await supabase.from('import_batches')
        .insert({ brand_id: brand.id, filename, row_count: rows.length }).select('id').single();
      if (bErr) throw bErr;
      const { data, error } = await supabase.rpc('import_people', {
        _brand_id: brand.id, _import_id: batch.id, _rows: applyMapping(rows, mapping),
      });
      if (error) throw error;
      setResult({ ok: data as ImportResult });
      qc.invalidateQueries({ queryKey: ['leads', brand.id] });
    } catch (e: any) {
      setResult({ error: e.message });
    } finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Import CSV</h1>
      <input type="file" accept=".csv" onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
      {mapping && (
        <>
          <p style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-sm)' }}>{rows.length} rows in {filename}</p>
          <div style={{ display: 'grid', gap: 'var(--space-2)', margin: 'var(--space-4) 0' }}>
            {FIELDS.map(f => (
              <label key={f} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center', fontSize: 'var(--font-sm)' }}>
                <span style={{ width: 130, color: 'var(--fg-tertiary)' }}>{f}</span>
                <select value={mapping[f] ?? ''} onChange={e => setMapping({ ...mapping, [f]: e.target.value || null })}>
                  <option value="">— skip —</option>
                  {headers.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
            ))}
          </div>
          <button onClick={runImport} disabled={busy}>{busy ? 'Importing…' : `Import ${rows.length} rows`}</button>
        </>
      )}
      {result && 'error' in result && (
        <div style={{ background: 'var(--tag-red-bg)', color: 'var(--tag-red-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', margin: 'var(--space-4) 0', fontSize: 'var(--font-sm)' }}>
          Import failed: {result.error}
        </div>
      )}
      {result && 'ok' in result && (
        <div style={{ margin: 'var(--space-4) 0' }}>
          <div style={{ background: 'var(--tag-green-bg)', color: 'var(--tag-green-fg)', padding: 'var(--space-3)',
            borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)' }}>
            Imported {result.ok.inserted} · Restored {result.ok.restored} · Skipped {result.ok.skipped}
            {result.ok.errors.length > 0 && ` · ${result.ok.errors.length} row${result.ok.errors.length === 1 ? '' : 's'} had errors`}
          </div>
          {result.ok.errors.length > 0 && (
            <pre style={{ background: 'var(--bg-secondary)', padding: 'var(--space-3)', borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-sm)', overflowX: 'auto', marginTop: 'var(--space-2)' }}>
              {JSON.stringify(result.ok.errors, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
