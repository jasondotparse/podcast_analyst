CREATE TABLE thesis_assessments (
    claim_id UUID NOT NULL REFERENCES claims ON DELETE CASCADE,
    thesis_id UUID NOT NULL REFERENCES theses ON DELETE CASCADE,
    stance TEXT NOT NULL CHECK (stance IN ('supports', 'challenges', 'unrelated')),
    strength SMALLINT NOT NULL CHECK (strength BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, thesis_id)
);
CREATE INDEX thesis_assessments_thesis_id_idx ON thesis_assessments (thesis_id);
