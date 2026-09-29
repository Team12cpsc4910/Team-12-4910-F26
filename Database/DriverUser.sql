CREATE TABLE DriverUser (
    driver_id INT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    points INT NOT NULL DEFAULT 0,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,

    PRIMARY KEY (driver_id),
    UNIQUE (user_id),
    FOREIGN KEY (user_id) REFERENCES UserAccount(user_id),
    FOREIGN KEY (sponsor_id) REFERENCES Sponsor(sponsor_id)
);
