CREATE TABLE Team12_DB.ProductCatalog (
  product_id INT NOT NULL AUTO_INCREMENT,
  sponsor_id INT NOT NULL,
  product_name VARCHAR(100) NOT NULL,
  `description` VARCHAR(255),
  point_cost INT NOT NULL,
  `availability` BOOLEAN,
  image_url VARCHAR(500),

  PRIMARY KEY (product_id),
  FOREIGN KEY (sponsor_id) REFERENCES Team12_DB.Sponsor(sponsor_id)
);
