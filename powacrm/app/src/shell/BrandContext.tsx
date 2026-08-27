import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';

export type Brand = { id: string; name: string; confidence_threshold: number; dry_run: boolean; paused: boolean };
const Ctx = createContext<{ brand: Brand; brands: Brand[]; setBrandId: (id: string) => void } | null>(null);

export function BrandProvider({ children }: { children: ReactNode }) {
  const brandsQuery = useQuery({
    queryKey: ['brands'],
    queryFn: async () => {
      const { data, error } = await supabase.from('brands')
        .select('id,name,confidence_threshold,dry_run,paused').order('created_at');
      if (error) throw error;
      return data as Brand[];
    },
  });
  const { data: brands, error } = brandsQuery;
  const [brandId, setBrandId] = useState<string | null>(null);

  // Three distinct states used to collapse into one permanent "Loading brands…":
  // a real query failure, a working project whose seed was never run (zero
  // brands), and the genuine in-flight case. The first two never resolve on
  // their own, so each needs to say so.
  if (error) {
    return (
      <div style={{ padding: 'var(--space-6)' }}>
        <p style={{ color: 'var(--tag-red-fg)' }}>Couldn't load brands: {error.message}</p>
        <button onClick={() => brandsQuery.refetch()}>Retry</button>
      </div>
    );
  }
  if (brands && brands.length === 0) {
    return (
      <p style={{ padding: 'var(--space-6)' }}>
        No brands found — did you run <code>db/seed/seed_gpt_trainer.sql</code>?
      </p>
    );
  }
  if (!brands) return <p style={{ padding: 'var(--space-6)' }}>Loading brands…</p>;
  const brand = brands.find(b => b.id === brandId) ?? brands[0];
  return <Ctx.Provider value={{ brand, brands, setBrandId }}>{children}</Ctx.Provider>;
}
export function useBrand() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useBrand outside BrandProvider');
  return v;
}
