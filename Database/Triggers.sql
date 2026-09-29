DELIMITER //

CREATE TRIGGER log_new_app
AFTER INSERT ON DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO DriverAppLog (app_id, reason, datetime)
    VALUES (NEW.app_id, 'App submitted', CURRENT_TIMESTAMP);
END//


CREATE TRIGGER log_updated_app
AFTER UPDATE ON DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO DriverAppLog (app_id, reason, datetime)
    VALUES (NEW.app_id, 'App updated', CURRENT_TIMESTAMP);
END//

CREATE TRIGGER log_deleted_app
AFTER DELETE ON DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO DriverAppLog (app_id, reason, datetime)
    VALUES (OLD.app_id, 'App deleted', CURRENT_TIMESTAMP);
END//