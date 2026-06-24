-- Phase 1B — 09_book_cluster_assignments
CREATE TABLE IF NOT EXISTS book_cluster_assignments (
  book_id UUID PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
  cluster_id INT NOT NULL,
  cluster_version TEXT NOT NULL,
  distance FLOAT,
  assigned_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cluster_members
  ON book_cluster_assignments (cluster_id, cluster_version);

ALTER TABLE book_cluster_assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cluster_read ON book_cluster_assignments;
CREATE POLICY cluster_read ON book_cluster_assignments FOR SELECT USING (TRUE);
