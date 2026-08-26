import { describe, it, expect } from 'vitest';
import { positionBetween } from './position';

describe('positionBetween', () => {
  it('returns 0 in an empty column', () => expect(positionBetween(null, null)).toBe(0));
  it('goes below the first card', () => expect(positionBetween(null, 5)).toBe(4));
  it('goes past the last card', () => expect(positionBetween(3, null)).toBe(4));
  it('takes the fractional midpoint between neighbors', () => expect(positionBetween(1, 2)).toBe(1.5));
});
