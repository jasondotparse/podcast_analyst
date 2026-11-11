-- Claim ranking from docs/etl_design.md#claim-ranking. The `rank` step scores each
-- claim on three 0-100 axes and derives `signal` from them; NULL means not ranked yet.
-- podcast_reader can SELECT these through 002's default privileges.
ALTER TABLE claims
    ADD COLUMN specificity SMALLINT CHECK (specificity BETWEEN 0 AND 100),
    ADD COLUMN materiality SMALLINT CHECK (materiality BETWEEN 0 AND 100),
    ADD COLUMN source_standing SMALLINT CHECK (source_standing BETWEEN 0 AND 100),
    ADD COLUMN signal SMALLINT CHECK (signal BETWEEN 0 AND 100);
