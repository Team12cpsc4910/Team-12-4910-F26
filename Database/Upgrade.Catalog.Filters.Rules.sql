ALTER TABLE Team12_DB.ProductCatalog
  ADD COLUMN category VARCHAR(50) NOT NULL DEFAULT 'Other',
  ADD COLUMN content_rating ENUM('Unrated', 'G', 'PG') NOT NULL DEFAULT 'Unrated',
  ADD COLUMN auto_managed BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN catalog_enabled BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE Team12_DB.CatalogGenerationRules (
  sponsor_id INT NOT NULL PRIMARY KEY,
  min_points INT NOT NULL DEFAULT 1,
  max_points INT NOT NULL DEFAULT 1000000,
  allowed_categories VARCHAR(255) NOT NULL DEFAULT 'Games,Gift Cards,Electronics,Entertainment,Other',
  allow_g BOOLEAN NOT NULL DEFAULT TRUE,
  allow_pg BOOLEAN NOT NULL DEFAULT TRUE,

  FOREIGN KEY (sponsor_id)
    REFERENCES Team12_DB.Sponsor(sponsor_id),

  CHECK (min_points BETWEEN 1 AND 1000000),
  CHECK (max_points BETWEEN min_points AND 1000000)
);

CREATE INDEX idx_catalog_category
  ON Team12_DB.ProductCatalog (
    sponsor_id,
    catalog_enabled,
    content_rating,
    category
  );
