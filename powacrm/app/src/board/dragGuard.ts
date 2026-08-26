// Tells a trailing click apart from a genuine one after a drag.
//
// `useDraggable().transform` (dnd-kit) resets to `null` synchronously the
// moment a drag ends — before the browser's native `click` event fires on
// the same pointerup. Because React re-renders with `transform === null`
// before that click is dispatched, checking `!transform` inside `onClick`
// sees the exact same value for "just finished a drag" and "plain click" —
// it can't tell them apart. This guard is a plain boolean flip that isn't
// tied to a render at all: `onDragEnd` sets it synchronously (dnd-kit calls
// it from the same pointerup handling that precedes the native click), so by
// the time the click fires the flag is already `true`. It resets on the next
// tick (after the click has had its chance to observe it) or immediately on
// the next drag start, whichever comes first.
export function createDragGuard() {
  let justDragged = false;
  let clearTimer: ReturnType<typeof setTimeout> | undefined;

  return {
    onDragStart(): void {
      if (clearTimer !== undefined) clearTimeout(clearTimer);
      justDragged = false;
    },
    onDragEnd(): void {
      justDragged = true;
      clearTimer = setTimeout(() => { justDragged = false; }, 0);
    },
    get justDragged(): boolean {
      return justDragged;
    },
  };
}

export type DragGuard = ReturnType<typeof createDragGuard>;
