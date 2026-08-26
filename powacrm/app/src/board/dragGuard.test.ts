import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createDragGuard } from './dragGuard';

describe('createDragGuard', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('starts false (a plain click, with no preceding drag, must navigate)', () => {
    const g = createDragGuard();
    expect(g.justDragged).toBe(false);
  });

  it('is true immediately after onDragEnd — the trailing click must see this', () => {
    const g = createDragGuard();
    g.onDragEnd();
    expect(g.justDragged).toBe(true);
  });

  it('clears itself on the next tick, so a later unrelated click still navigates', () => {
    const g = createDragGuard();
    g.onDragEnd();
    vi.advanceTimersByTime(0);
    expect(g.justDragged).toBe(false);
  });

  it('onDragStart clears a stale flag immediately, not just after a tick', () => {
    const g = createDragGuard();
    g.onDragEnd();
    expect(g.justDragged).toBe(true);
    g.onDragStart();
    expect(g.justDragged).toBe(false);
  });

  it('onDragStart cancels the previous drag\'s pending clear timer', () => {
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout');
    const g = createDragGuard();
    g.onDragEnd(); // schedules a clear timer
    g.onDragStart(); // a new drag starts before that timer fires
    // Without cancelling the stale timer, it could later fire mid-way through
    // the new drag (or right after the new drag's own onDragEnd) and clear
    // the flag at the wrong moment. onDragStart must clear it explicitly.
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});
