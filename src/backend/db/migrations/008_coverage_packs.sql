CREATE TABLE coverage (
    entity_id UUID PRIMARY KEY REFERENCES entities ON DELETE CASCADE,
    ticker TEXT NOT NULL UNIQUE,
    thesis_id UUID NOT NULL UNIQUE REFERENCES theses,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE debates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES coverage ON DELETE CASCADE,
    label TEXT NOT NULL,
    title TEXT NOT NULL,
    our_view TEXT NOT NULL,
    counter_view TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX debates_active_identity ON debates (entity_id, title, our_view, counter_view) WHERE is_active;
CREATE INDEX debates_entity_id_idx ON debates (entity_id);
CREATE TABLE research_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debate_id UUID NOT NULL REFERENCES debates ON DELETE CASCADE,
    text TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX research_questions_active_identity ON research_questions (debate_id, text) WHERE is_active;
CREATE INDEX research_questions_debate_id_idx ON research_questions (debate_id);
CREATE TABLE model_drivers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES coverage ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE (entity_id, name)
);
CREATE TABLE question_drivers (
    question_id UUID NOT NULL REFERENCES research_questions ON DELETE CASCADE,
    driver_id UUID NOT NULL REFERENCES model_drivers ON DELETE CASCADE,
    PRIMARY KEY (question_id, driver_id)
);
CREATE INDEX question_drivers_driver_id_idx ON question_drivers (driver_id);
