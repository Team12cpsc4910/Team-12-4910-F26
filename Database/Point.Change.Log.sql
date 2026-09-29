CREATE TABLE PointChangeLog (
    point_log_id BIGINT NOT NULL AUTO_INCREMENT,
    driver_id INT NOT NULL,
    -- Sponsor user who made the change; NULL for older rows or changes not made by a sponsor user
    sponsor_user_id INT NULL,
    points_change INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    datetime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (point_log_id),
    FOREIGN KEY (driver_id)REFERENCES DriverUser(driver_id),
    FOREIGN KEY (sponsor_user_id) REFERENCES SponsorUser(sponsor_user_id)
);
