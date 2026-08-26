export function StageTag({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ background: `var(--tag-${color}-bg)`, color: `var(--tag-${color}-fg)`,
      padding: '1px var(--space-2)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--font-xs)', fontWeight: 500 }}>
      {label}
    </span>
  );
}
