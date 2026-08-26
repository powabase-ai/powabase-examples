import { useState } from 'react';
import type { FormEvent } from 'react';
import { supabase } from '@/lib/powabase';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setError(error.message);
  }
  return (
    <form onSubmit={submit} style={{ maxWidth: 320, margin: '20vh auto', display: 'grid', gap: 'var(--space-3)' }}>
      <h1 style={{ fontSize: 'var(--font-xl)' }}>PowaCRM</h1>
      <input placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} />
      <input placeholder="Password" type="password" value={password} onChange={e => setPassword(e.target.value)} />
      <button type="submit">Sign in</button>
      {error && <p style={{ color: 'var(--tag-red-fg)' }}>{error}</p>}
    </form>
  );
}
