import { useRef } from 'react';
import { DndContext, useDraggable, useDroppable, type DragEndEvent } from '@dnd-kit/core';
import { useNavigate } from 'react-router-dom';
import { useBrand } from '@/shell/BrandContext';
import { positionBetween } from '@/lib/position';
import { StageTag } from './StageTag';
import { createDragGuard, type DragGuard } from './dragGuard';
import { groupByStage, leadName, useLeads, useMoveLead, useStages, type Lead, type Stage } from './useLeads';

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
  const { data: stages } = useStages();
  const { data: leads } = useLeads(brand.id);
  const move = useMoveLead(brand.id);
  const dragGuard = useRef(createDragGuard()).current;
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
    <DndContext onDragStart={() => dragGuard.onDragStart()} onDragEnd={onDragEnd}>
      <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>Pipeline</h1>
      <div style={{ display: 'flex', gap: 'var(--space-4)', overflowX: 'auto' }}>
        {stages.map(s => <Column key={s.value} stage={s} leads={grouped[s.value]} dragGuard={dragGuard} />)}
      </div>
    </DndContext>
  );
}
