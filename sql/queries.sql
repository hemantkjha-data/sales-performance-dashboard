-- ============================================================
-- Sales Performance Dashboard — key SQL queries (SQLite)
-- Dataset: 50,000 orders, Jan 2024 – Dec 2025
-- ============================================================

-- 1. Executive KPIs
SELECT SUM(net_revenue) total_revenue,
       SUM(profit) total_profit,
       ROUND(100.0*SUM(profit)/SUM(net_revenue),2) margin_pct,
       COUNT(*) orders,
       SUM(qty) units_sold
FROM sales;

-- 2. Monthly revenue & profit trend (MoM growth via window function)
WITH monthly AS (
  SELECT substr(date,1,7) AS ym,
         SUM(net_revenue) revenue,
         SUM(profit) profit
  FROM sales GROUP BY ym
)
SELECT ym, revenue, profit,
       ROUND(100.0*(revenue - LAG(revenue) OVER (ORDER BY ym))
             /LAG(revenue) OVER (ORDER BY ym),2) mom_growth_pct
FROM monthly ORDER BY ym;

-- 3. Regional performance
SELECT region, SUM(net_revenue) revenue, SUM(profit) profit,
       ROUND(100.0*SUM(profit)/SUM(net_revenue),2) margin_pct,
       COUNT(*) orders
FROM sales GROUP BY region ORDER BY revenue DESC;

-- 4. Category performance
SELECT category, SUM(net_revenue) revenue, SUM(profit) profit,
       ROUND(100.0*SUM(profit)/SUM(net_revenue),2) margin_pct
FROM sales GROUP BY category ORDER BY revenue DESC;

-- 5. Channel mix
SELECT channel, SUM(net_revenue) revenue, COUNT(*) orders,
       ROUND(100.0*SUM(net_revenue)/(SELECT SUM(net_revenue) FROM sales),1) revenue_share_pct
FROM sales GROUP BY channel ORDER BY revenue DESC;

-- 6. Top 10 products by revenue
SELECT product, category, SUM(net_revenue) revenue, SUM(qty) units
FROM sales GROUP BY product ORDER BY revenue DESC LIMIT 10;

-- 7. ⭐ Segment margin heatmap — category × region (finds the weak spots)
SELECT category, region,
       SUM(net_revenue) revenue,
       ROUND(100.0*SUM(profit)/SUM(net_revenue),2) margin_pct,
       COUNT(*) orders
FROM sales GROUP BY category, region;

-- 8. ⭐ Margin-recovery opportunity: weak segments vs their category average
WITH seg AS (
  SELECT category, region,
         SUM(net_revenue) rev,
         100.0*SUM(profit)/SUM(net_revenue) seg_margin
  FROM sales GROUP BY category, region
), catavg AS (
  SELECT category, 100.0*SUM(profit)/SUM(net_revenue) cat_margin
  FROM sales GROUP BY category
)
SELECT s.category, s.region,
       ROUND(s.rev/10000000.0,2) revenue_cr,
       ROUND(s.seg_margin,2) segment_margin_pct,
       ROUND(c.cat_margin,2) category_avg_pct,
       ROUND((c.cat_margin - s.seg_margin)/100 * s.rev/10000000.0, 2) profit_uplift_cr
FROM seg s JOIN catavg c ON s.category = c.category
ORDER BY (c.cat_margin - s.seg_margin) DESC
LIMIT 5;

-- 9. Discount impact: do discounted orders actually protect margin?
SELECT CASE WHEN discount > 0 THEN 'Discounted' ELSE 'Full price' END AS grp,
       COUNT(*) orders,
       SUM(net_revenue) revenue,
       ROUND(100.0*SUM(profit)/SUM(net_revenue),2) margin_pct
FROM sales GROUP BY grp;

-- 10. Sales velocity — month × region × category cube (drives the dashboard slicers)
SELECT substr(date,1,7) AS ym, region, category,
       SUM(net_revenue) revenue, SUM(profit) profit,
       COUNT(*) orders, SUM(qty) units
FROM sales GROUP BY ym, region, category ORDER BY ym;
