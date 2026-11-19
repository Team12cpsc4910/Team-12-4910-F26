CREATE TABLE Team12_DB.PointAwardConfig (
    sponsor_id INT NOT NULL,
    points_per_award INT NOT NULL DEFAULT 10,
    award_reason VARCHAR(255) NOT NULL DEFAULT 'Recurring performance award',
    `enabled` BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at DATETIME NULL,

    PRIMARY KEY (sponsor_id),
    FOREIGN KEY (sponsor_id) REFERENCES Team12_DB.Sponsor(sponsor_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_points_positive CHECK (points_per_award > 0)
);