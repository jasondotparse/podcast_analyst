-- Which entities each thesis is about; mirrored as (Thesis)-[:ABOUT]->(Entity) in the graph.
-- podcast_reader can SELECT it through the default privileges set in 002.
CREATE TABLE thesis_entities (
    thesis_id UUID NOT NULL REFERENCES theses ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES entities ON DELETE CASCADE,
    PRIMARY KEY (thesis_id, entity_id)
);
CREATE INDEX thesis_entities_entity_id_idx ON thesis_entities (entity_id);
