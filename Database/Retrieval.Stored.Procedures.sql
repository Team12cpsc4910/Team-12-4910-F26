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

DELIMITER ;