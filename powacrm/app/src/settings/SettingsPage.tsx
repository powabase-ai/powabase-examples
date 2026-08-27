import { useEffect, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { useBrand, type Brand } from '@/shell/BrandContext';

export function SettingsPage() {
  const { brand } = useBrand();
  const qc = useQueryClient();

  // Local draft state, reset whenever the active brand changes (switching
  // brands in the nav select while this page is open should not leave the
  // previous brand's half-edited draft sitting in these fields).
  const [name, setName] = useState(brand.name);
  const [productDescription, setProductDescription] = useState(brand.product_description ?? '');
  const [voiceNotes, setVoiceNotes] = useState(brand.voice_notes ?? '');
  const [icpNotes, setIcpNotes] = useState(brand.icp_notes ?? '');
  const [dailyCap, setDailyCap] = useState(String(brand.research_daily_cap));

  useEffect(() => {
    setName(brand.name);
    setProductDescription(brand.product_description ?? '');
    setVoiceNotes(brand.voice_notes ?? '');
    setIcpNotes(brand.icp_notes ?? '');
    setDailyCap(String(brand.research_daily_cap));
  }, [brand.id]);

  const save = useMutation({
    mutationFn: async (fields: Partial<Brand>) => {
      const { error } = await supabase.from('brands').update(fields).eq('id', brand.id);
      if (error) throw error;
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['brands'] }),
  });

  // A blank or negative cap used to be silently discarded -- the mutation
  // fell back to the brand's prior value while every other field on the form
  // saved, with nothing telling the user their edit to THIS field didn't
  // take. Validate up front instead: compute whether the cap is usable, show
  // why when it isn't, and refuse to submit at all rather than quietly drop
  // just that one field.
  const parsedCap = Number.parseInt(dailyCap, 10);
  const capError = dailyCap.trim() === ''
    ? 'Required.'
    : !Number.isFinite(parsedCap) || parsedCap < 0
      ? 'Must be a whole number, 0 or greater.'
      : null;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (capError) return;
    save.mutate({
      name: name.trim() || brand.name,
      product_description: productDescription.trim() || null,
      voice_notes: voiceNotes.trim() || null,
      icp_notes: icpNotes.trim() || null,
      research_daily_cap: parsedCap,
    });
  }

  const labelStyle: CSSProperties = {
    display: 'block', fontSize: 'var(--font-sm)', color: 'var(--fg-tertiary)', marginBottom: 'var(--space-1)',
  };
  const fieldStyle: CSSProperties = {
    width: '100%', fontFamily: 'inherit', fontSize: 'var(--font-md)', color: 'var(--fg-primary)',
    background: 'var(--bg-primary)', border: '1px solid var(--border-medium)', borderRadius: 'var(--radius-sm)',
    padding: 'var(--space-2)',
  };

  return (
    <div style={{ maxWidth: 640 }}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Settings</h1>

      {save.error && (
        <div style={{ background: 'var(--tag-red-bg)', color: 'var(--tag-red-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)', marginBottom: 'var(--space-3)' }}>
          {/* Without this a rejected write just snapped the fields back on the next
              refetch, which reads as "I mistyped it" rather than "the server refused". */}
          Couldn't save: {save.error.message}
        </div>
      )}

      <form onSubmit={submit} style={{ display: 'grid', gap: 'var(--space-4)' }}>
        <div>
          <label style={labelStyle} htmlFor="settings-name">Brand name</label>
          <input id="settings-name" style={fieldStyle} value={name} onChange={e => setName(e.target.value)} />
        </div>

        <div>
          <label style={labelStyle} htmlFor="settings-product">Product description</label>
          <textarea id="settings-product" style={{ ...fieldStyle, minHeight: 80, resize: 'vertical' }}
            value={productDescription} onChange={e => setProductDescription(e.target.value)}
            placeholder="What you sell and who it's for." />
        </div>

        <div>
          <label style={labelStyle} htmlFor="settings-voice">Voice notes</label>
          <textarea id="settings-voice" style={{ ...fieldStyle, minHeight: 80, resize: 'vertical' }}
            value={voiceNotes} onChange={e => setVoiceNotes(e.target.value)}
            placeholder="Tone and style notes for anything written on this brand's behalf." />
        </div>

        <div>
          <label style={labelStyle} htmlFor="settings-icp">ICP notes</label>
          <p style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)', marginTop: 0, marginBottom: 'var(--space-2)' }}>
            This is what the researcher scores every lead's company against to produce its fit score. Write it in
            plain English — the industry, company size, and signals that make a lead worth pursuing. Vague or empty
            notes here mean fit scores across the whole pipeline are not worth trusting.
          </p>
          <textarea id="settings-icp" style={{ ...fieldStyle, minHeight: 160, resize: 'vertical' }}
            value={icpNotes} onChange={e => setIcpNotes(e.target.value)}
            placeholder="e.g. B2B SaaS companies, 20-200 employees, with an outbound sales team and no existing CRM automation." />
        </div>

        <div>
          <label style={labelStyle} htmlFor="settings-cap">Research daily cap</label>
          <p style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)', marginTop: 0, marginBottom: 'var(--space-2)' }}>
            Maximum research jobs this brand may enqueue per day. Every run spends credits.
          </p>
          <input id="settings-cap" type="number" min={0} step={1} style={{ ...fieldStyle, maxWidth: 120 }}
            value={dailyCap} onChange={e => setDailyCap(e.target.value)}
            aria-invalid={capError ? true : undefined} aria-describedby={capError ? 'settings-cap-error' : undefined} />
          {capError && (
            <p id="settings-cap-error" style={{ fontSize: 'var(--font-xs)', color: 'var(--tag-red-fg)',
              marginTop: 'var(--space-1)', marginBottom: 0 }}>
              {capError}
            </p>
          )}
        </div>

        <div>
          <button type="submit" disabled={save.isPending || !!capError}>{save.isPending ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </div>
  );
}
