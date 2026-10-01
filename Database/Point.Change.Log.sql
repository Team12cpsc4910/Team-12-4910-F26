CREATE TABLE Team12_DB.PointChangeLog (
    point_log_id BIGINT NOT NULL AUTO_INCREMENT,
    driver_id INT NOT NULL,
    sponsor_user_id INT NULL,
    points_change INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    `datetime` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (point_log_id),
    FOREIGN KEY (driver_id)REFERENCES Team12_DB.DriverUser(driver_id),
    FOREIGN KEY (sponsor_user_id) REFERENCES Team12_DB.SponsorUser(sponsor_user_id)
);
