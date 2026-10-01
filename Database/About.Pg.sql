CREATE TABLE AboutPage (
  about_id INT PRIMARY KEY,
  team_number INT NOT NULL,
  version_number INT NOT NULL,
  release_date DATE NOT NULL,
  product_name VARCHAR(100) NOT NULL,
  product_description VARCHAR(500) NOT NULL
);

INSERT INTO AboutPage (
  about_id,
  team_number,
  version_number,
  release_date,
  product_name,
  product_description
  )

VALUES (
  1,
  12,
  4,
  '2026-09-17',
-- wasnt sure what date to use so i uded todays 
  'Good Driver Insentive',
  'An app that rewards truck drivers with points for good driving and points are redeamable for items in the catalog'
  );
