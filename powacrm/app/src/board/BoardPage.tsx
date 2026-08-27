import { useRef } from 'react';
import { DndContext, PointerSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { useNavigate } from 'react-router-dom';
import { useBrand } from '@/shell/BrandContext';
import { positionBetween } from '@/lib/position';
import { StageTag } from './StageTag';
import { createDragGuard, type DragGuard } from './dragGuard';
import { groupByStage, leadName, useLeads, useMoveLead, useStages, LEAD_CAP, type Lead, type Stage } from './useLeads';

function Card({ lead, dragGuard }: { lead: Lead; dragGuard: DragGuard }) {
  const nav = useNavigate();
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: lead.id, data: lead });
  return (
    <div ref={setNodeRef} {...attributes} {...listeners}
      onClick={() => { if (!dragGuard.justDragged) nav(`/leads/${lead.id}`); }}
      style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-medium)',
        borderRadius: 'var(--radius-md)', padding: 'var(--space-3)', cursor: 'grab',
        transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined }}>
      <div style={{ fontWeight: 500 }}>{leadName(lead)}</div>
      <div style={{ fontSize: 'var(--font-sm)', color: 'var(--fg-tertiary)' }}>
        {[lead.title, lead.company?.name].filter(Boolean).join(' · ')}
      </div>
      {lead.fit_score != null && <div style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)' }}>fit {lead.fit_score}</div>}
    </div>
  );
}

function Column({ stage, leads, dragGuard }: { stage: Stage; leads: Lead[]; dragGuard: DragGuard }) {
  const { setNodeRef } = useDroppable({ id: stage.value });
  return (
    <div ref={setNodeRef} style={{ width: 260, flexShrink: 0, display: 'grid', gap: 'var(--space-2)', alignContent: 'start' }}>
      <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
        <StageTag label={stage.label} color={stage.color} />
        <span style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)' }}>{leads.length}</span>
      </div>
      {leads.map(l => <Card key={l.id} lead={l} dragGuard={dragGuard} />)}
    </div>
  );
}

export function BoardPage() {
  const { brand } = useBrand();
  const stagesQuery = useStages();
  const leadsQuery = useLeads(brand.id);
  const move = useMoveLead(brand.id);
  const dragGuard = useRef(createDragGuard()).current;
  // dnd-kit's default sensors are a PointerSensor with NO activationConstraint,
  // and AbstractPointerSensor starts the drag from its own constructor in that
  // case. A plain click therefore fires a phantom onDragStart/onDragEnd AND
  // installs a document capture-phase `click` listener that stopPropagation()s
  // the click before React sees it -- which made every card unclickable and the
  // whole /leads/:id route unreachable. An activation distance means the drag
  // only begins once the pointer has actually moved.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  const { data: stages, error: stagesError } = stagesQuery;
  const { data: leads, error: leadsError } = leadsQuery;
  const error = stagesError ?? leadsError;
  if (error && !leads) {
    return (
      <div>
        <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Pipeline</h1>
        <p style={{ color: 'var(--tag-red-fg)' }}>Couldn't load the pipeline: {error.message}</p>
        <button onClick={() => { stagesQuery.refetch(); leadsQuery.refetch(); }}>Retry</button>
      </div>
    );
  }
  if (!stages || !leads) return <p>Loading…</p>;
  const grouped = groupByStage(leads, stages.map(s => s.value));

  function onDragEnd(e: DragEndEvent) {
    dragGuard.onDragEnd();
    const lead = e.active.data.current as Lead;
    const toStage = e.over?.id as string | undefined;
    if (!toStage || toStage === lead.stage) return;
    const col = grouped[toStage];
    const last = col.length ? col[col.length - 1].position : null;
    move.mutate({ lead, toStage, position: positionBetween(last, null) });
  }

  return (
    <DndContext sensors={sensors} onDragStart={() => dragGuard.onDragStart()} onDragEnd={onDragEnd}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Pipeline</h1>
      {leads.length >= LEAD_CAP && (
        <div style={{ background: 'var(--tag-orange-bg)', color: 'var(--tag-orange-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)', marginBottom: 'var(--space-3)' }}>
          Showing the first {LEAD_CAP} leads by position. This brand has more, so the per-column
          counts below count what is on screen, not what the brand holds.
        </div>
      )}
      {move.error && (
        <div style={{ background: 'var(--tag-red-bg)', color: 'var(--tag-red-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)', marginBottom: 'var(--space-3)' }}>
          {/* A refused move animates into the new column and snaps back. With no
              reason on screen that reads as the app losing the drag, and invites
              the user to retry against a refusal that will not change. LeadPage's
              two write paths say so; the board's third should too. */}
          Couldn't move that lead: {move.error.message}
        </div>
      )}
      <div style={{ display: 'flex', gap: 'var(--space-4)', overflowX: 'auto' }}>
        {stages.map(s => <Column key={s.value} stage={s} leads={grouped[s.value]} dragGuard={dragGuard} />)}
      </div>
    </DndContext>
  );
}
