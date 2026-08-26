import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';

export type Brand = { id: string; name: string; confidence_threshold: number; dry_run: boolean; paused: boolean };
const Ctx = createContext<{ brand: Brand; brands: Brand[]; setBrandId: (id: string) => void } | null>(null);

export function BrandProvider({ children }: { children: ReactNode }) {
  const { data: brands } = useQuery({
    queryKey: ['brands'],
    queryFn: async () => {
      const { data, error } = await supabase.from('brands')
        .select('id,name,confidence_threshold,dry_run,paused').order('created_at');
      if (error) throw error;
      return data as Brand[];
    },
  });
  const [brandId, setBrandId] = useState<string | null>(null);
  if (!brands || brands.length === 0) return <p style={{ padding: 'var(--space-6)' }}>Loading brands…</p>;
  const brand = brands.find(b => b.id === brandId) ?? brands[0];
  return <Ctx.Provider value={{ brand, brands, setBrandId }}>{children}</Ctx.Provider>;
}
export function useBrand() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useBrand outside BrandProvider');
  return v;
}
