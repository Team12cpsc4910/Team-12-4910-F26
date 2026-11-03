CREATE TABLE Team12_DB.DriverSponsor (
    driver_sponsor_id INT NOT NULL AUTO_INCREMENT,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    points INT NOT NULL DEFAULT 0 CHECK (points >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'Active',
    joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (driver_sponsor_id),
    UNIQUE KEY uk_driver_sponsor (driver_id, sponsor_id),
    FOREIGN KEY (driver_id) REFERENCES Team12_DB.DriverUser(driver_id),
    FOREIGN KEY (sponsor_id) REFERENCES Team12_DB.Sponsor(sponsor_id),
    INDEX idx_driver_sponsor_sponsor (sponsor_id, status)
);