-- Run this in Supabase SQL Editor → New query

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS public.accessories (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  external_product_id TEXT UNIQUE NOT NULL,
  title               TEXT NOT NULL,
  price               NUMERIC(10, 2) NOT NULL DEFAULT 0,
  currency            TEXT NOT NULL DEFAULT 'PKR',
  image_url           TEXT,
  product_url         TEXT NOT NULL,
  category            TEXT,
  subcategory         TEXT,
  description         TEXT,
  seller_name         TEXT,
  source_platform     TEXT NOT NULL DEFAULT 'daraz',
  tags                TEXT[],
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_accessories_category  ON public.accessories (category);
CREATE INDEX IF NOT EXISTS idx_accessories_is_active ON public.accessories (is_active);
CREATE INDEX IF NOT EXISTS idx_accessories_price     ON public.accessories (price);

-- Full-text search
CREATE INDEX IF NOT EXISTS idx_accessories_fts ON public.accessories
  USING GIN (to_tsvector('english',
    coalesce(title,'') || ' ' || coalesce(description,'') || ' ' ||
    coalesce(array_to_string(tags,' '),'')
  ));

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_accessories_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

DROP TRIGGER IF EXISTS trg_accessories_updated_at ON public.accessories;
CREATE TRIGGER trg_accessories_updated_at
  BEFORE UPDATE ON public.accessories
  FOR EACH ROW EXECUTE FUNCTION update_accessories_updated_at();

-- RLS
ALTER TABLE public.accessories ENABLE ROW LEVEL SECURITY;

CREATE POLICY "public read active accessories"
  ON public.accessories FOR SELECT USING (is_active = TRUE);

CREATE POLICY "service role full access"
  ON public.accessories FOR ALL USING (auth.role() = 'service_role');
