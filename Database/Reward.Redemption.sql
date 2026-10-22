CREATE TABLE IF NOT EXISTS RewardRedemption (
    redemption_id BIGINT NOT NULL AUTO_INCREMENT,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    product_id INT NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    points_spent INT NOT NULL,
    request_key CHAR(32) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'Requested',
    redeemed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fulfilled_at DATETIME NULL,
    PRIMARY KEY (redemption_id),
    FOREIGN KEY (driver_id) REFERENCES DriverUser(driver_id),
    FOREIGN KEY (sponsor_id) REFERENCES Sponsor(sponsor_id),
    FOREIGN KEY (product_id) REFERENCES ProductCatalog(product_id),
    INDEX redemption_driver_idx (driver_id, redeemed_at),
    INDEX redemption_sponsor_idx (sponsor_id, status),
    CONSTRAINT reward_points_positive CHECK (points_spent > 0)
);
