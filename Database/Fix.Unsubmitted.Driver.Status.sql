SELECT d.driver_id, u.username, d.application_status
FROM Team12_DB.DriverUser AS d
JOIN Team12_DB.UserAccount AS u ON d.user_id = u.user_id
WHERE d.application_status = 'Pending'
  AND NOT EXISTS (
      SELECT 1 FROM Team12_DB.DriverApp AS a
      WHERE a.driver_id = d.driver_id
  );
