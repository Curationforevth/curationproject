-- Phase 1B — 13_functions_co_occurrence
CREATE OR REPLACE FUNCTION aggregate_co_occurrence() RETURNS void AS $$
BEGIN
  CREATE TEMP TABLE tmp_co ON COMMIT DROP AS
    SELECT
      LEAST(a.book_id, b.book_id) AS book_a_id,
      GREATEST(a.book_id, b.book_id) AS book_b_id,
      COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') AS co_like_count,
      COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') AS co_save_count
    FROM user_books a
    JOIN user_books b ON a.user_id = b.user_id AND a.book_id < b.book_id
    WHERE (a.rating='good' OR a.status='wishlist')
      AND (b.rating='good' OR b.status='wishlist')
    GROUP BY 1, 2
    HAVING COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') >= 3
        OR COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') >= 3;

  DELETE FROM book_co_occurrence;
  INSERT INTO book_co_occurrence (book_a_id, book_b_id, co_like_count, co_save_count, updated_at)
    SELECT book_a_id, book_b_id, co_like_count, co_save_count, NOW() FROM tmp_co;
END;
$$ LANGUAGE plpgsql;
