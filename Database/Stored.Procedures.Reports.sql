USE Team12_DB;

DELIMITER //

CREATE PROCEDURE GetAdminSalesReport(
    IN param_sponsor_id INT,
    IN param_driver_id INT,
    IN param_start_date DATETIME,
    IN param_end_date DATETIME
)
BEGIN
    SELECT 
        rr.redemption_id,
        rr.redeemed_at,
        s.sponsor_id,
        s.sponsor_name,
        du.driver_id,
        du.first_name AS driver_first_name,
        du.last_name AS driver_last_name,
        rr.product_name,
        rr.points_spent,
        rr.status
    FROM RewardRedemption rr
    INNER JOIN Sponsor s ON rr.sponsor_id = s.sponsor_id
    INNER JOIN DriverUser du ON rr.driver_id = du.driver_id
    WHERE (
          param_sponsor_id IS NULL
          OR rr.sponsor_id = param_sponsor_id
      )
      AND (
          param_driver_id IS NULL
          OR rr.driver_id = param_driver_id
      )
      AND (
          param_start_date IS NULL
          OR rr.redeemed_at >= param_start_date
      )
      AND (
          param_end_date IS NULL
          OR rr.redeemed_at <= param_end_date
      )
    ORDER BY rr.redeemed_at DESC;
END //

CREATE PROCEDURE GetSponsorDriverPointReport(
    IN param_sponsor_user_id INT,
    IN param_driver_id INT
)
BEGIN
    DECLARE val_sponsor_id INT;

    SELECT sponsor_id
    INTO val_sponsor_id
    FROM SponsorUser
    WHERE sponsor_user_id = param_sponsor_user_id;

    SELECT 
        ds.driver_id,
        du.first_name,
        du.last_name,
        ds.points AS current_points,
        COALESCE(SUM(pcl.points_change), 0) AS total_historical_activity
    FROM DriverSponsor ds
    INNER JOIN DriverUser du ON ds.driver_id = du.driver_id
    LEFT JOIN PointChangeLog pcl 
        ON ds.driver_id = pcl.driver_id 
        AND ds.sponsor_id = pcl.sponsor_id
    WHERE ds.sponsor_id = val_sponsor_id
      AND (
          param_driver_id IS NULL
          OR ds.driver_id = param_driver_id
      )
    GROUP BY ds.driver_id, du.first_name, du.last_name, ds.points
    ORDER BY du.last_name ASC, du.first_name ASC;
END //

CREATE PROCEDURE GetAdminDriverSalesSummary(
    IN param_sponsor_id INT,
    IN param_driver_id INT
)
BEGIN
    SELECT 
        s.sponsor_id,
        s.sponsor_name,
        du.driver_id,
        du.first_name,
        du.last_name,
        COUNT(rr.redemption_id) AS total_redemptions,
        COALESCE(SUM(rr.points_spent), 0) AS total_points_spent
    FROM RewardRedemption rr
    INNER JOIN Sponsor s ON rr.sponsor_id = s.sponsor_id
    INNER JOIN DriverUser du ON rr.driver_id = du.driver_id
    WHERE (
          param_sponsor_id IS NULL
          OR rr.sponsor_id = param_sponsor_id
      )
      AND (
          param_driver_id IS NULL
          OR rr.driver_id = param_driver_id
      )
    GROUP BY 
        s.sponsor_id, 
        s.sponsor_name, 
        du.driver_id, 
        du.first_name, 
        du.last_name
    ORDER BY 
        s.sponsor_name ASC, 
        total_points_spent DESC;
END //

DELIMITER ;