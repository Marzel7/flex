-- Add peak grade hold duration tracking to token_behavior
-- peak_grade:           G-class achieved at peak MC (e.g. "G5")
-- peak_grade_reached_at: unix timestamp when that grade was first hit
-- peak_grade_held_secs: seconds the token held its peak grade before dropping
ALTER TABLE token_behavior ADD COLUMN peak_grade TEXT;
ALTER TABLE token_behavior ADD COLUMN peak_grade_reached_at INTEGER;
ALTER TABLE token_behavior ADD COLUMN peak_grade_held_secs INTEGER;

CREATE INDEX IF NOT EXISTS idx_tb_peak_grade
    ON token_behavior(peak_grade, peak_grade_held_secs);
