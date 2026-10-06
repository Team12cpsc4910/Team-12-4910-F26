DELIMITER //

--DRIVER APPS
CREATE TRIGGER log_new_app
AFTER INSERT ON Team12_DB.DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO Team12_DB.DriverAppLog (app_id, reason, datetime)
    VALUES (NEW.app_id, 'App submitted', CURRENT_TIMESTAMP);
END//


CREATE TRIGGER log_updated_app
AFTER UPDATE ON Team12_DB.DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO Team12_DB.DriverAppLog (app_id, reason, datetime)
    VALUES (NEW.app_id, 'App updated', CURRENT_TIMESTAMP);
END//

CREATE TRIGGER log_deleted_app
AFTER DELETE ON Team12_DB.DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO Team12_DB.DriverAppLog (app_id, reason, datetime)
    VALUES (OLD.app_id, 'App deleted', CURRENT_TIMESTAMP);
END//

--DRIVER POINT CHANGE
CREATE TRIGGER log_points_change
AFTER UPDATE ON Team12_DB.DriverUser
FOR EACH ROW
BEGIN
    IF OLD.points <> NEW.points THEN
        IF (NEW.points - OLD.points) > 0 THEN
            INSERT INTO Team12_DB.PointChangeLog (
                driver_id,
                points_change,
                reason
            )
            VALUES (
                NEW.driver_id,
                NEW.points - OLD.points,
                'Points added'
            );
        ELSE
            INSERT INTO Team12_DB.PointChangeLog (
                driver_id,
                points_change,
                reason
            )
            VALUES (
                NEW.driver_id,
                NEW.points - OLD.points,
                'Points removed'
            );
        END IF;
    END IF;
END//

--RESET LOCKED STATUS AND LOGIN ATTEMPTS
CREATE TRIGGER password_reset
AFTER INSERT ON Team12_DB.PasswordChangeLog
FOR EACH ROW
BEGIN
    UPDATE Team12_DB.UserAccount
    SET locked = FALSE, login_attempts = 0
    WHERE user_id = NEW.user_id;
END//

--RESET FAILED LOGIN ATTEMPTS
CREATE TRIGGER Team12_DB.reset_failed_logins
AFTER INSERT ON Team12_DB.LoginAttemptLog
FOR EACH ROW
BEGIN
    IF NEW.status = 'Success' THEN
        UPDATE Team12_DB.UserAccount
        SET failed_logins = 0
        WHERE user_id = NEW.user_id;
    END IF;
END//

DELIMITER ;