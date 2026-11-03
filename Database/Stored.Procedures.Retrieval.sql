USE Team12_DB;

DELIMITER //

CREATE PROCEDURE GetPasswordChangeLogs(
    IN param_user_id INT
)
BEGIN
    DECLARE val_user_type VARCHAR(20);
    DECLARE val_sponsor_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT user_type
    INTO val_user_type
    FROM UserAccount
    WHERE user_id = param_user_id;

    IF val_user_type = 'Driver' THEN
        SELECT *
        FROM PasswordChangeLog
        WHERE user_id = param_user_id
        ORDER BY `datetime` DESC;

    ELSEIF val_user_type = 'Sponsor' THEN
        SELECT sponsor_id
        INTO val_sponsor_id
        FROM SponsorUser
        WHERE user_id = param_user_id;

        SELECT PasswordChangeLog.*
        FROM PasswordChangeLog
        INNER JOIN DriverUser
            ON PasswordChangeLog.user_id = DriverUser.user_id
        WHERE DriverUser.sponsor_id = val_sponsor_id
        ORDER BY PasswordChangeLog.`datetime` DESC;

    ELSEIF val_user_type = 'Admin' THEN
        SELECT *
        FROM PasswordChangeLog
        ORDER BY `datetime` DESC;

    END IF;
END //

CREATE PROCEDURE GetLoginAttemptLogs(
    IN param_user_id INT
)
BEGIN
    SELECT *
    FROM LoginAttemptLog
    WHERE user_id = param_user_id
    ORDER BY `datetime` DESC;
END //

CREATE PROCEDURE GetUserAccountInfo(
    IN param_user_id INT
)
BEGIN
    DECLARE val_user_type VARCHAR(20);

    SELECT user_type
    INTO val_user_type
    FROM UserAccount
    WHERE user_id = param_user_id;

    IF val_user_type = 'Driver' THEN
        SELECT *
        FROM UserAccount
        INNER JOIN DriverUser
            ON UserAccount.user_id = DriverUser.user_id
        WHERE UserAccount.user_id = param_user_id;

    ELSEIF val_user_type = 'Sponsor' THEN
        SELECT *
        FROM UserAccount
        INNER JOIN SponsorUser
            ON UserAccount.user_id = SponsorUser.user_id
        WHERE UserAccount.user_id = param_user_id;

    ELSEIF val_user_type = 'Admin' THEN
        SELECT *
        FROM UserAccount
        INNER JOIN AdminUser
            ON UserAccount.user_id = AdminUser.user_id
        WHERE UserAccount.user_id = param_user_id;

    END IF;
END //

CREATE PROCEDURE GetSponsorPointChangeLogs(
    IN param_sponsor_user_id INT,
    IN param_driver_id INT,
    IN param_start_date DATETIME,
    IN param_end_date DATETIME
)
BEGIN
    SELECT PointChangeLog.*
    FROM PointChangeLog
    INNER JOIN DriverUser
        ON PointChangeLog.driver_id = DriverUser.driver_id
    INNER JOIN SponsorUser
        ON DriverUser.sponsor_id = SponsorUser.sponsor_id
    WHERE SponsorUser.sponsor_user_id = param_sponsor_user_id
      AND (
          param_driver_id IS NULL
          OR PointChangeLog.driver_id = param_driver_id
      )
      AND (
          param_start_date IS NULL
          OR PointChangeLog.`datetime` >= param_start_date
      )
      AND (
          param_end_date IS NULL
          OR PointChangeLog.`datetime` <= param_end_date
      )
    ORDER BY PointChangeLog.`datetime` DESC;
END //

DELIMITER ;