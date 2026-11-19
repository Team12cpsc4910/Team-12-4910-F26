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
        'Not Applied',
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

CREATE PROCEDURE GenerateSponsorInvoice(
    IN param_sponsor_id INT,
    IN param_month INT, -- 1-12 for January to December
    IN param_year INT
)
BEGIN
    DECLARE val_point_value DECIMAL(10,2);
    
    -- Get the sponsor's point-to-dollar conversion rate
    SELECT point_value INTO val_point_value 
    FROM Sponsor WHERE sponsor_id = param_sponsor_id//

    -- Return the invoice summary
    SELECT 
        s.sponsor_id,
        s.sponsor_name,
        param_month AS billing_month,
        param_year AS billing_year,
        COUNT(rr.redemption_id) AS total_redemptions,
        COALESCE(SUM(rr.points_spent), 0) AS total_points_spent,
        (COALESCE(SUM(rr.points_spent), 0) * val_point_value) AS total_amount_owed,
        CURRENT_TIMESTAMP AS generated_at
    FROM RewardRedemption rr
    INNER JOIN Sponsor s ON rr.sponsor_id = s.sponsor_id
    WHERE rr.sponsor_id = param_sponsor_id
      AND MONTH(rr.redeemed_at) = param_month
      AND YEAR(rr.redeemed_at) = param_year
    GROUP BY s.sponsor_id, s.sponsor_name//

    -- Return the line items for the invoice
    SELECT 
        rr.redemption_id,
        rr.redeemed_at,
        du.first_name AS driver_first_name,
        du.last_name AS driver_last_name,
        rr.product_name,
        rr.points_spent,
        (rr.points_spent * val_point_value) AS line_item_cost
    FROM RewardRedemption rr
    INNER JOIN DriverUser du ON rr.driver_id = du.driver_id
    WHERE rr.sponsor_id = param_sponsor_id
      AND MONTH(rr.redeemed_at) = param_month
      AND YEAR(rr.redeemed_at) = param_year
    ORDER BY rr.redeemed_at ASC//
END//

DELIMITER ;
