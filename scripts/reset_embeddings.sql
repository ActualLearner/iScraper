-- One-time reset after switching embedding models.
--
-- Embeddings are only comparable within a single model's vector space, so when you
-- change EMBEDDING_MODEL (e.g. migrating off Gemini to a local model), the stored
-- vectors must be discarded and recomputed. This nulls them out; the worker's
-- backfill (embed_pending_posts) refills every post on subsequent runs.
--
-- If the new model's dimension differs from the current vector(...) column, run the
-- ALTER below FIRST (and update match_source_posts in init_db.sql to match), then
-- re-run init_db.sql, then this reset.
--
--   alter table source_posts alter column embedding type vector(384);  -- example

update source_posts set embedding = null;
