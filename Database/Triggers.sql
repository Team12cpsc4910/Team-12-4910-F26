CREATE TRIGGER log_new_app
AFTER INSERT ON DriverApp
FOR EACH ROW
BEGIN
    INSERT INTO DriverAppLog (app_id, reason, datetime)
    VALUES (NEW.app_id, 'App submitted', CURRENT_TIMESTAMP);
END;