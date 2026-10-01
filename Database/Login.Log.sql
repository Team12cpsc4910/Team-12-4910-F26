CREATE TABLE Team12_DB.LoginAttemptLog (
    login_log_id BIGINT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    `status` VARCHAR(30) NOT NULL,
    `datetime` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (login_log_id),
    FOREIGN KEY (user_id) REFERENCES Team12_DB.UserAccount(user_id)
);
