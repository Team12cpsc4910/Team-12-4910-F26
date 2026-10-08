CREATE TABLE Team12_DB.Sponsor ( 
  sponsor_id INT NOT NULL AUTO_INCREMENT,
  sponsor_name VARCHAR(100) NOT NULL,
  point_value DECIMAL(10,2) NOT NULL DEFAULT 0,

  PRIMARY KEY (sponsor_id)
);
