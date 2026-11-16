CREATE TABLE question_assessments (
    claim_id UUID NOT NULL REFERENCES claims ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES research_questions ON DELETE CASCADE,
    stance TEXT NOT NULL CHECK (stance IN ('supports', 'challenges', 'neutral')),
    strength SMALLINT NOT NULL CHECK (strength BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, question_id)
);
CREATE INDEX question_assessments_question_id_idx ON question_assessments (question_id);
