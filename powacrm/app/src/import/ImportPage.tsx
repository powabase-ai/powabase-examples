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
  const [parseErrors, setParseErrors] = useState<{ row?: number; message: string; type?: string }[]>([]);

  function onFile(f: File) {
    // Clear everything up front. Setting only the filename here meant a second
    // file that failed to parse rendered the PREVIOUS file's row count under the
    // new name -- and imported the previous file's rows.
    setFilename(f.name); setResult(null); setHeaders([]); setRows([]); setMapping(null); setParseErrors([]);
    Papa.parse<Record<string, string>>(f, {
      header: true,
      // 'greedy' also drops lines that are only separators (",,,"). Plain `true`
      // keeps them, and a row of empty cells maps to an object with no fields,
      // which used to import as a blank lead counted as a success.
      skipEmptyLines: 'greedy',
      complete: res => {
        const hs = res.meta.fields ?? [];
        // Papa reports malformed rows here rather than throwing. A stray unquoted
        // comma yields TooManyFields and shifts every value one column left for
        // that row, so importing it silently would write the wrong data.
        setParseErrors(res.errors ?? []);
        setHeaders(hs); setRows(res.data); setMapping(guessMapping(hs));
      },
      error: err => setResult({ error: `Could not read ${f.name}: ${err.message}` }),
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
          {parseErrors.length > 0 && (
            <div style={{ background: 'var(--tag-orange-bg)', color: 'var(--tag-orange-fg)', padding: 'var(--space-3)',
              borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)', margin: 'var(--space-2) 0' }}>
              {parseErrors.length} row{parseErrors.length === 1 ? '' : 's'} in this file are malformed — most often a stray
              unquoted comma, which shifts that row's values one column left. Check them before importing:
              <pre style={{ marginTop: 'var(--space-2)', overflowX: 'auto' }}>
                {parseErrors.slice(0, 5).map(e => `row ${e.row ?? '?'}: ${e.message}`).join('\n')}
                {parseErrors.length > 5 ? `\n…and ${parseErrors.length - 5} more` : ''}
              </pre>
            </div>
          )}
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
      {result && 'ok' in result && (() => {
        const { inserted, restored, skipped, errors } = result.ok;
        const landed = inserted + restored + skipped;
        // Colour follows what actually happened. Reading "ok" off the presence of
        // a response meant a call where every row failed -- which the database
        // honestly stamps status='failed' -- still rendered green, and colour is
        // read before text.
        const tone = errors.length === 0 ? 'green' : landed === 0 ? 'red' : 'orange';
        const headline = errors.length === 0
          ? `Imported ${inserted} · Restored ${restored} · Skipped ${skipped}`
          : landed === 0
            ? `Import failed — every row errored (${errors.length})`
            : `Imported with problems — ${inserted} in, ${restored} restored, ${skipped} skipped, ${errors.length} failed`;
        return (
        <div style={{ margin: 'var(--space-4) 0' }}>
          <div style={{ background: `var(--tag-${tone}-bg)`, color: `var(--tag-${tone}-fg)`, padding: 'var(--space-3)',
            borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)' }}>
            {headline}
          </div>
          {errors.length > 0 && (
            <pre style={{ background: 'var(--bg-secondary)', padding: 'var(--space-3)', borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-sm)', overflowX: 'auto', marginTop: 'var(--space-2)' }}>
              {JSON.stringify(errors, null, 2)}
            </pre>
          )}
        </div>
        );
      })()}
    </div>
  );
}
