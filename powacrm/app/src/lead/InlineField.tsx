import { useRef, useState } from 'react';

export function InlineField({ label, value, onSave }:
  { label: string; value: string | null; onSave: (v: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  // Enter calls commit() directly and then blurs the input (to leave edit
  // mode), which would otherwise fire the onBlur handler's own commit() a
  // second time with the same draft. Guard with a ref so only the first of
  // the two fires calls onSave.
  const committing = useRef(false);
  function open() { setDraft(value ?? ''); setEditing(true); committing.current = false; }
  function commit() {
    if (committing.current) return;
    committing.current = true;
    setEditing(false);
    const v = draft.trim() || null;
    if (v !== value) onSave(v);
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minHeight: 28 }}>
      <span style={{ width: 90, flexShrink: 0, fontSize: 'var(--font-sm)', color: 'var(--fg-tertiary)' }}>{label}</span>
      {editing ? (
        <input autoFocus value={draft} onChange={e => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={e => {
            if (e.key === 'Enter') { commit(); e.currentTarget.blur(); }
            if (e.key === 'Escape') { committing.current = true; setEditing(false); }
          }} />
      ) : (
        <span onClick={open} style={{ cursor: 'pointer', borderRadius: 'var(--radius-sm)',
          padding: '2px var(--space-1)', color: value ? 'var(--fg-primary)' : 'var(--fg-light)' }}
          onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
          {value ?? 'Empty'}
        </span>
      )}
    </div>
  );
}
