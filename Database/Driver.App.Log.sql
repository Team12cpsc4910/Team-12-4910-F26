CREATE TABLE Team12_DB.DriverAppLog (
    app_log_id BIGINT NOT NULL AUTO_INCREMENT,
    app_id INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    `datetime` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (app_log_id),
    FOREIGN KEY (app_id) REFERENCES Team12_DB.DriverApp(app_id)
);