import { describe, it, expect } from 'vitest';
import { guessMapping, applyMapping } from './mapColumns';

describe('guessMapping', () => {
  it('matches common header variants case-insensitively', () => {
    const m = guessMapping(['First Name', 'LAST_NAME', 'E-mail', 'Job Title', 'LinkedIn URL', 'Company', 'Website']);
    expect(m.first_name).toBe('First Name');
    expect(m.last_name).toBe('LAST_NAME');
    expect(m.email).toBe('E-mail');
    expect(m.title).toBe('Job Title');
    expect(m.linkedin_url).toBe('LinkedIn URL');
    expect(m.company_name).toBe('Company');
    expect(m.company_domain).toBe('Website');
  });
  it('leaves unmatched fields null', () => {
    expect(guessMapping(['foo']).email).toBeNull();
  });
});

describe('applyMapping', () => {
  it('projects rows and extracts registrable domain from URLs', () => {
    const rows = [{ Website: 'https://www.acme.com/about', Email: 'a@b.c' }];
    const out = applyMapping(rows, { first_name: null, last_name: null, email: 'Email',
      title: null, linkedin_url: null, company_name: null, company_domain: 'Website' });
    expect(out[0].company_domain).toBe('acme.com');
    expect(out[0].email).toBe('a@b.c');
  });
});
