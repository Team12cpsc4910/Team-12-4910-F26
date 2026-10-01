CREATE TABLE Team12_DB.PasswordChangeLog (
    password_log_id BIGINT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    changed_by_user_id INT NOT NULL,
    change_type VARCHAR(30) NOT NULL,
    `datetime` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (password_log_id),
    FOREIGN KEY (user_id) REFERENCES Team12_DB.UserAccount(user_id),
    FOREIGN KEY (changed_by_user_id) REFERENCES Team12_DB.UserAccount(user_id)
);
