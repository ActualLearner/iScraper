-- One-time reset / migration after switching embedding models.
--
-- Embeddings are only comparable within a single model's vector space, so when you
-- change EMBEDDING_MODEL (e.g. migrating off Gemini, or from a 768-dim model to the
-- local 384-dim default BAAI/bge-small-en-v1.5), the stored vectors must be
-- discarded and recomputed. The worker's backfill (embed_pending_posts) refills
-- every post on subsequent runs.
--
-- Run this in the Supabase SQL editor. Steps 1-2 are safe to re-run.

-- 1. Drop the now-incompatible vectors (required before changing the dimension).
update source_posts set embedding = null;

-- 2. Resize the column to the new model's dimension. With every value already null
--    this is a fast metadata change. Skip if the dimension is unchanged.
alter table source_posts alter column embedding type vector(384);

-- 3. Re-run scripts/init_db.sql afterwards so match_source_posts() is recreated
--    with the matching vector(384) signature (create-or-replace; safe to re-run).
