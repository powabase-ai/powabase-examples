import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_POWABASE_URL as string;
const anonKey = import.meta.env.VITE_POWABASE_ANON_KEY as string;
if (!url || !anonKey) throw new Error('Set VITE_POWABASE_URL and VITE_POWABASE_ANON_KEY in app/.env.local');

export const supabase = createClient(url, anonKey);
