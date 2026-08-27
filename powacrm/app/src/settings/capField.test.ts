import { describe, it, expect } from 'vitest';
import { RESEARCH_CAP_MAX, validateResearchCap } from './capField';

describe('validateResearchCap', () => {
  it('accepts an ordinary cap', () => {
    expect(validateResearchCap('25')).toEqual({ value: 25, error: null });
  });
  it('accepts both endpoints, because 0 pauses research and the ceiling must not be off by one', () => {
    expect(validateResearchCap('0')).toEqual({ value: 0, error: null });
    expect(validateResearchCap(String(RESEARCH_CAP_MAX))).toEqual({ value: RESEARCH_CAP_MAX, error: null });
  });
  it('refuses the value a stranger actually PATCHed, and names the ceiling', () => {
    const v = validateResearchCap('100000');
    expect(v.value).toBeNull();
    expect(v.error).toContain(String(RESEARCH_CAP_MAX));
    expect(v.error).toContain('credits');
  });
  it('refuses one past the ceiling', () => {
    expect(validateResearchCap(String(RESEARCH_CAP_MAX + 1)).value).toBeNull();
  });
  it('refuses a negative cap and says what 0 is for', () => {
    const v = validateResearchCap('-5');
    expect(v.value).toBeNull();
    expect(v.error).toContain('pause');
  });
  it('refuses an empty field rather than silently keeping the old value', () => {
    expect(validateResearchCap('   ')).toEqual({ value: null, error: 'Required.' });
  });
  it('refuses input parseInt would have silently truncated', () => {
    // parseInt('12abc') is 12 and parseInt('1e3') is 1 -- both would save a
    // number the user never typed.
    expect(validateResearchCap('12abc').value).toBeNull();
    expect(validateResearchCap('1e3').value).toBeNull();
    expect(validateResearchCap('25.5').value).toBeNull();
  });
});
