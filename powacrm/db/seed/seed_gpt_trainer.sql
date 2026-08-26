-- Idempotent by construction: brands.name has no unique constraint, so a bare
-- `ON CONFLICT DO NOTHING` (no arbiter index/constraint to match) would not
-- stop a second run from inserting a duplicate 'gpt-trainer' row. Guard with
-- an explicit existence check instead -- safe for this single-writer,
-- sequential seed script (no concurrent-insert race to worry about).
INSERT INTO brands (name, product_description, voice_notes)
SELECT 'gpt-trainer',
       'gpt-trainer: no-code platform to build, train, and deploy custom AI chatbots and agents on your own data.',
       'Founder voice: direct, technical, helpful. No competitor bashing. No hype-chains.'
WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'gpt-trainer');
