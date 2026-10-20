-- Supabase (Postgres) tables used by the pipeline and dashboard.
-- Reconstructed from the code for anyone who wants to run the project.

create table articles (
  id           bigint generated always as identity primary key,
  title        text not null,
  url          text not null unique,
  source_name  text not null,
  published_at timestamptz,
  body_text    text
);

create table claims (
  id            bigint generated always as identity primary key,
  article_id    bigint references articles(id) on delete cascade,
  claim_text    text not null,
  source_name   text,
  article_title text,
  published_at  timestamptz
);

create table rhetorical_scores (
  id                  bigint generated always as identity primary key,
  article_id          bigint references articles(id) on delete cascade,
  emotional_intensity int check (emotional_intensity between 1 and 5),
  certainty_language  int check (certainty_language between 1 and 5),
  attribution_clarity int check (attribution_clarity between 1 and 5),
  framing_cues        int check (framing_cues between 1 and 5),
  risk_score          numeric
);

create table fact_check_cache (
  id         bigint generated always as identity primary key,
  claim_text text not null,
  results    jsonb
);
