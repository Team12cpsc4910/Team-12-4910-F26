USE Team12_DB;

DELIMITER //

CREATE PROCEDURE CreateDriverAccount(
    IN param_username VARCHAR(50),
    IN param_email VARCHAR(100),
    IN param_password_hash VARCHAR(255),
    IN param_sponsor_id INT,
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE val_user_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO UserAccount (
        username,
        email,
        password_hash,
        user_type
    )
    VALUES (
        param_username,
        param_email,
        param_password_hash,
        'Driver'
    );

    SET val_user_id = LAST_INSERT_ID();

    INSERT INTO DriverUser (
        user_id,
        sponsor_id,
        points,
        application_status,
        first_name,
        last_name
    )
    VALUES (
        val_user_id,
        param_sponsor_id,
        0,
        'Pending',
        param_first_name,
        param_last_name
    );

    COMMIT;
END //

CREATE PROCEDURE CreateSponsorAccount(
    IN param_username VARCHAR(50),
    IN param_email VARCHAR(100),
    IN param_password_hash VARCHAR(255),
    IN param_sponsor_id INT,
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE val_user_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO UserAccount (
        username,
        email,
        password_hash,
        user_type
    )
    VALUES (
        param_username,
        param_email,
        param_password_hash,
        'Sponsor'
    );

    SET val_user_id = LAST_INSERT_ID();

    INSERT INTO SponsorUser (
        user_id,
        sponsor_id,
        first_name,
        last_name
    )
    VALUES (
        val_user_id,
        param_sponsor_id,
        param_first_name,
        param_last_name
    );

    COMMIT;
END //

CREATE PROCEDURE CreateAdminAccount(
    IN param_username VARCHAR(50),
    IN param_email VARCHAR(100),
    IN param_password_hash VARCHAR(255),
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE val_user_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO UserAccount (
        username,
        email,
        password_hash,
        user_type
    )
    VALUES (
        param_username,
        param_email,
        param_password_hash,
        'Admin'
    );

    SET val_user_id = LAST_INSERT_ID();

    INSERT INTO AdminUser (
        user_id,
        first_name,
        last_name
    )
    VALUES (
        val_user_id,
        param_first_name,
        param_last_name
    );

    COMMIT;
END //

CREATE PROCEDURE CreateSponsor(
    IN param_sponsor_name VARCHAR(100),
    IN param_point_value DECIMAL(10,2)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO Sponsor (
        sponsor_name,
        point_value
    )
    VALUES (
        param_sponsor_name,
        param_point_value
    );

    COMMIT;
END //

CREATE PROCEDURE CreateLoginLog(
    IN param_user_id INT,
    IN param_status VARCHAR(20)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    INSERT INTO LoginAttemptLog (
        user_id,
        status
    )
    VALUES (
        param_user_id,
        param_status
    );

    COMMIT;
END //

DELIMITER ;