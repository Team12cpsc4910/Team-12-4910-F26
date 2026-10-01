  CREATE TABLE Team12_DB.AdminUser (
    admin_user_id INT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    first_name VARCHAR(50),
    last_name VARCHAR(50),

    PRIMARY KEY (admin_user_id),
    FOREIGN KEY (user_id) REFERENCES Team12_DB.UserAccount(user_id)
);
