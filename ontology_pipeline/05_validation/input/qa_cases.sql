-- Small schema fixtures for deterministic regression checks, not LLM responses.
CREATE TABLE parents(a TEXT NOT NULL, b TEXT NOT NULL, PRIMARY KEY(a,b));
CREATE TABLE child_unique(
  id INTEGER PRIMARY KEY, a TEXT, b TEXT,
  FOREIGN KEY(a,b) REFERENCES parents, UNIQUE(a,b)
);
CREATE TABLE child_partial(
  id INTEGER PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL,
  FOREIGN KEY(a,b) REFERENCES parents(a,b)
);
CREATE UNIQUE INDEX child_partial_key ON child_partial(a,b) WHERE id > 0;
CREATE TABLE expressions(id INTEGER PRIMARY KEY, label TEXT);
CREATE UNIQUE INDEX expression_key ON expressions(lower(label));
CREATE TABLE nodes(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES nodes(id));
CREATE TABLE nullable_pk(id TEXT PRIMARY KEY, value INTEGER,
  doubled INTEGER GENERATED ALWAYS AS (value * 2) STORED);
CREATE TABLE strict_pk(id TEXT PRIMARY KEY) STRICT;
CREATE TABLE without_rowid(id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE case_child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES NODES(ID));
CREATE TABLE repeated_fk(id INTEGER PRIMARY KEY, parent_id INTEGER,
  FOREIGN KEY(parent_id) REFERENCES nodes(id),
  FOREIGN KEY(parent_id) REFERENCES nodes(id)
);
