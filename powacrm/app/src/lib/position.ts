// Fractional-index ordering: one PATCH per drop, no sibling reindex. [Twenty]
export function positionBetween(prev: number | null, next: number | null): number {
  if (prev === null && next === null) return 0;
  if (prev === null) return (next as number) - 1;
  if (next === null) return prev + 1;
  return (prev + next) / 2;
}
