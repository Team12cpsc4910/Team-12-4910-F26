-- Run once on the existing database to add the "who made the change" column to PointChangeLog.
ALTER TABLE PointChangeLog
    ADD COLUMN sponsor_user_id INT NULL AFTER driver_id,
    ADD FOREIGN KEY (sponsor_user_id) REFERENCES SponsorUser(sponsor_user_id);
