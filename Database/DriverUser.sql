CREATE TABLE Team12_DB.DriverUser (
    driver_id INT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    points INT NOT NULL DEFAULT 0 CHECK (points >= 0),
    application_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,

    PRIMARY KEY (driver_id),
    FOREIGN KEY (user_id) REFERENCES Team12_DB.UserAccount(user_id),
    FOREIGN KEY (sponsor_id) REFERENCES Team12_DB.Sponsor(sponsor_id)
);
