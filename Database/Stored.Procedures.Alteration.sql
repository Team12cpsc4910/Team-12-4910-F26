USE Team12_DB;

DELIMITER //

CREATE PROCEDURE UpdateDriverAccount(
    IN param_driver_id INT,
    IN param_sponsor_id INT,
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    UPDATE DriverUser
    SET sponsor_id = param_sponsor_id,
        first_name = param_first_name,
        last_name = param_last_name
    WHERE driver_id = param_driver_id;

    COMMIT;
END //

CREATE PROCEDURE UpdateSponsorAccount(
    IN param_sponsor_user_id INT,
    IN param_sponsor_id INT,
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    UPDATE SponsorUser
    SET sponsor_id = param_sponsor_id,
        first_name = param_first_name,
        last_name = param_last_name
    WHERE sponsor_user_id = param_sponsor_user_id;

    COMMIT;
END //

CREATE PROCEDURE UpdateAdminAccount(
    IN param_admin_user_id INT,
    IN param_first_name VARCHAR(50),
    IN param_last_name VARCHAR(50)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    UPDATE AdminUser
    SET first_name = param_first_name,
        last_name = param_last_name
    WHERE admin_user_id = param_admin_user_id;

    COMMIT;
END //

DELIMITER ;