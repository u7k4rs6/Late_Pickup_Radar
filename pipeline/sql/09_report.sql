-- Inputs for the evidence charts that are not already rows of metrics.csv.

-- name: wait_histogram
-- 0.05-decade bins of the waits that enter the KPI (positive waits only on a log axis).
SELECT floor(log10(wait_minutes) * 20)::INTEGER AS bin_k, count(*) AS n
FROM model.fact_trip
WHERE NOT flag_r12 AND wait_minutes > 0  -- families: wait
GROUP BY bin_k
ORDER BY bin_k;
