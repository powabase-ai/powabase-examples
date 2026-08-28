// Regression test for "board cards are not clickable".
//
// dnd-kit's default sensors use PointerSensor with NO activationConstraint, and
// AbstractPointerSensor calls handleStart() straight from the constructor in that
// case. handleStart() (a) fires onDragStart/onDragEnd for a plain click -- a
// phantom drag that arms our dragGuard -- and (b) installs a document-level
// CAPTURE `click` listener that calls stopPropagation(), so the click never
// reaches React at all. Both effects independently swallow the navigation, which
// made the whole /leads/:id route unreachable from the board.
//
// The fix is an activation distance (see BoardPage's `useSensors`). This test
// renders the real BoardPage against a stubbed PostgREST client and asserts a
// plain click navigates, and that a genuine drag past the threshold does not.
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { BoardPage } from './BoardPage';
import { BrandProvider } from '@/shell/BrandContext';

vi.mock('@/lib/powabase', () => {
  const DATA: Record<string, unknown[]> = {
    brands: [{ id: 'brand-1', name: 'Test Brand', confidence_threshold: 0.7, dry_run: true, paused: false }],
    stage_options: [
      { value: 'sourced', label: 'Sourced', color: 'blue', position: 1 },
      { value: 'researched', label: 'Researched', color: 'green', position: 2 },
    ],
    people: [{
      id: 'lead-1', first_name: 'Cara', last_name: 'Vance', title: 'CTO', email: 'cara@example.com',
      stage: 'sourced', position: 1, fit_score: null, company: null,
    }],
    events: [],
  };
  // Minimal chainable stand-in for the supabase-js query builder: every builder
  // method returns the chain, and the chain itself is awaitable.
  const chain = (table: string) => {
    const self: Record<string, unknown> = {
      then: (res: (v: unknown) => unknown, rej?: (e: unknown) => unknown) =>
        Promise.resolve({ data: DATA[table] ?? [], error: null }).then(res, rej),
    };
    for (const m of ['select', 'eq', 'is', 'limit', 'order', 'insert', 'update', 'single']) {
      self[m] = () => self;
    }
    return self;
  };
  return { supabase: { from: (table: string) => chain(table) } };
});

// jsdom implements PointerEvent, but older/partial environments do not. Shim a
// MouseEvent-backed stand-in so the sensor's `isPrimary`/`button` checks work.
beforeAll(() => {
  if (typeof window.PointerEvent !== 'function') {
    class ShimPointerEvent extends MouseEvent {
      pointerId: number; pointerType: string; isPrimary: boolean;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
        this.pointerType = init.pointerType ?? 'mouse';
        this.isPrimary = init.isPrimary ?? true;
      }
    }
    (window as unknown as { PointerEvent: unknown }).PointerEvent = ShimPointerEvent;
  }
});

// vitest runs without `globals`, so @testing-library/react's auto-cleanup never
// registers itself -- unmount explicitly or the second render sees both boards.
afterEach(cleanup);

function pointer(type: string, x: number, y: number) {
  return new window.PointerEvent(type, {
    bubbles: true, cancelable: true, composed: true,
    pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, buttons: type === 'pointerup' ? 0 : 1,
    clientX: x, clientY: y,
  });
}

async function renderBoard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>
        <BrandProvider>
          <Routes>
            <Route path="/" element={<BoardPage />} />
            <Route path="/leads/:id" element={<div>LEAD DETAIL ROUTE</div>} />
          </Routes>
        </BrandProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const card = await screen.findByText('Cara Vance');
  return card.parentElement as HTMLElement;
}

describe('BoardPage card click', () => {
  it('navigates to the lead route on a plain click (no drag)', async () => {
    const card = await renderBoard();

    await act(async () => {
      card.dispatchEvent(pointer('pointerdown', 100, 100));
      card.dispatchEvent(pointer('pointerup', 100, 100));
      card.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true, button: 0 }));
    });

    await waitFor(() => expect(screen.getByText('LEAD DETAIL ROUTE')).toBeTruthy());
  });

  it('does not navigate on the click that trails a real drag', async () => {
    const card = await renderBoard();

    await act(async () => {
      card.dispatchEvent(pointer('pointerdown', 100, 100));
      // Past the 8px activation distance -- the sensor listens on the document.
      document.dispatchEvent(pointer('pointermove', 160, 100));
    });
    // Guard against a vacuous pass: prove the drag really activated before
    // asserting the trailing click was swallowed.
    expect(screen.getByRole('status').textContent).toMatch(/Picked up draggable item lead-1/);

    await act(async () => {
      document.dispatchEvent(pointer('pointerup', 160, 100));
      card.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true, button: 0 }));
    });

    expect(screen.queryByText('LEAD DETAIL ROUTE')).toBeNull();
    expect(screen.queryByText('Cara Vance')).not.toBeNull();
  });
});
