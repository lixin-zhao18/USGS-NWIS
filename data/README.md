# Data policy

Raw USGS observations and large intermediate tables are downloaded locally and are not committed to Git.

- `data/raw/`: source-faithful API responses or parsed source chunks
- `data/interim/`: normalized and quality-controlled station series
- `data/processed/`: local event-level analysis tables

Committed experiment records contain source URLs/parameters, retrieval times, row counts, SHA-256 checksums, station selections, exclusion reasons, and compact summaries. This preserves provenance without duplicating mutable upstream services or committing hundreds of millions of observations.
