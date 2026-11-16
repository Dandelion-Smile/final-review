-- Keep failed/pending attempts owned by their generation. A cache key may have
-- several historical attempts; only reviewed 'done' results are reusable.
ALTER TABLE material_note_polish_batches
  DROP CONSTRAINT IF EXISTS material_note_polish_batches_user_id_cache_key_key;
