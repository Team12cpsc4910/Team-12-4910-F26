CREATE TABLE ProductCatalog (
  product_id INT NOT NULL AUTO_INCREMENT,
  sponsor_id INT NOT NULL,
  product_name VARCHAR(100) NOT NULL,
  [description] VARCHAR(255),
  point_cost INT NOT NULL,
  [availability] BOOLEAN,
  image_url VARCHAR(500),

  PRIMARY KEY (product_id),
  FOREIGN KEY (sponsor_id) REFERENCES Sponsor(sponsor_id)
);
