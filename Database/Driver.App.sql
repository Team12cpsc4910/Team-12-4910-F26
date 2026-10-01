CREATE TABLE Team12_DB.DriverApp (
    app_id INT NOT NULL AUTO_INCREMENT,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    app VARCHAR(255) NOT NULL, --this is a placeholder for actual stoarge of app info
    `state` VARCHAR(20) NOT NULL DEFAULT 'pending',

    PRIMARY KEY (app_id),
    FOREIGN KEY (driver_id) REFERENCES Team12_DB.DriverUser(driver_id),
    FOREIGN KEY (sponsor_id) REFERENCES Team12_DB.Sponsor(sponsor_id)
);