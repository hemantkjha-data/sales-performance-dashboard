"""
Sales Performance Dashboard — data pipeline
Ingest → clean → transform → load (SQLite) → aggregate

Run:  python pipeline.py
Needs: pandas, sqlite3 (stdlib)

Input :  data/sales_data.csv  (50,000 raw orders)
Output:  data/sales_data.db   (clean `sales` table)
         dashboard/agg_data.json (aggregates consumed by dashboard/index.html)
"""
import json
import sqlite3
import pandas as pd

CSV_PATH = "data/sales_data.csv"
DB_PATH = "data/sales_data.db"
AGG_PATH = "dashboard/agg_data.json"


def extract() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    assert len(df) == 50_000, f"unexpected row count: {len(df)}"
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    # types
    df["date"] = pd.to_datetime(df["date"])
    num_cols = ["qty", "gross_revenue", "discount", "net_revenue", "cost", "profit"]
    df[num_cols] = df[num_cols].astype("int64")

    # drop exact duplicates on the primary key
    before = len(df)
    df = df.drop_duplicates(subset="order_id", keep="first")
    dropped = before - len(df)
    if dropped:
        print(f"dropped {dropped} duplicate order_ids")

    # standardise categorical labels
    df["region"] = df["region"].str.strip().str.title()
    df["category"] = df["category"].str.strip().str.title()
    df["channel"] = df["channel"].str.strip().str.title()

    # integrity checks
    assert (df["gross_revenue"] >= 0).all(), "negative gross revenue found"
    assert (df["discount"] <= df["gross_revenue"]).all(), "discount exceeds gross revenue"
    assert (df["net_revenue"] == df["gross_revenue"] - df["discount"]).all(), "net_revenue mismatch"
    assert (df["profit"] == df["net_revenue"] - df["cost"]).all(), "profit mismatch"

    # derived columns
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["margin_pct"] = (100 * df["profit"] / df["net_revenue"]).round(2)
    return df


def load(df: pd.DataFrame, con: sqlite3.Connection) -> None:
    df.drop(columns=["margin_pct"]).to_sql("sales", con, if_exists="replace", index=False)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sales_region_cat ON sales(region, category)")
    con.commit()


def aggregate(con: sqlite3.Connection) -> dict:
    q = lambda sql: con.execute(sql).fetchall()
    agg = {}

    agg["kpi"] = dict(zip(
        ["revenue", "profit", "margin_pct", "orders", "units"],
        q("""SELECT SUM(net_revenue), SUM(profit),
                    ROUND(100.0*SUM(profit)/SUM(net_revenue),2),
                    COUNT(*), SUM(qty) FROM sales""")[0],
    ))

    agg["byMonth"] = [
        {"ym": r[0], "revenue": r[1], "profit": r[2], "orders": r[3]}
        for r in q("""SELECT substr(date,1,7), SUM(net_revenue), SUM(profit), COUNT(*)
                      FROM sales GROUP BY 1 ORDER BY 1""")
    ]

    for key, col in [("byRegion", "region"), ("byCategory", "category"), ("byChannel", "channel")]:
        agg[key] = [
            {col: r[0], "revenue": r[1], "profit": r[2], "orders": r[3]}
            for r in q(f"""SELECT {col}, SUM(net_revenue), SUM(profit), COUNT(*)
                           FROM sales GROUP BY 1 ORDER BY 2 DESC""")
        ]

    agg["topProducts"] = [
        {"product": r[0], "category": r[1], "revenue": r[2], "units": r[3]}
        for r in q("""SELECT product, category, SUM(net_revenue), SUM(qty)
                      FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 10""")
    ]

    agg["weakSegments"] = [
        {"category": r[0], "region": r[1], "revenue": r[2], "margin_pct": r[3], "orders": r[4]}
        for r in q("""SELECT category, region, SUM(net_revenue),
                             ROUND(100.0*SUM(profit)/SUM(net_revenue),2), COUNT(*)
                      FROM sales GROUP BY 1,2 ORDER BY 4 ASC LIMIT 8""")
    ]

    agg["discountImpact"] = [
        {"grp": r[0], "orders": r[1], "revenue": r[2], "margin_pct": r[3]}
        for r in q("""SELECT CASE WHEN discount>0 THEN 'Discounted' ELSE 'Full price' END,
                             COUNT(*), SUM(net_revenue),
                             ROUND(100.0*SUM(profit)/SUM(net_revenue),2)
                      FROM sales GROUP BY 1""")
    ]

    # month × region × category cube (drives live slicer recomputation)
    agg["cube"] = [
        {"ym": r[0], "region": r[1], "category": r[2], "revenue": r[3], "profit": r[4], "orders": r[5], "units": r[6]}
        for r in q("""SELECT substr(date,1,7), region, category,
                             SUM(net_revenue), SUM(profit), COUNT(*), SUM(qty)
                      FROM sales GROUP BY 1,2,3 ORDER BY 1""")
    ]

    # uplift simulation: weak segments raised to their category-average margin
    agg["uplift"] = [
        {"category": r[0], "region": r[1], "rev_cr": r[2], "seg_margin": r[3],
         "cat_avg_margin": r[4], "profit_uplift_cr": r[5]}
        for r in q("""
            WITH seg AS (
                SELECT category, region, SUM(net_revenue) rev,
                       100.0*SUM(profit)/SUM(net_revenue) mp
                FROM sales GROUP BY 1,2),
            catavg AS (
                SELECT category, 100.0*SUM(profit)/SUM(net_revenue) cmp
                FROM sales GROUP BY 1)
            SELECT s.category, s.region,
                   ROUND(s.rev/10000000.0,2), ROUND(s.mp,2), ROUND(c.cmp,2),
                   ROUND((c.cmp - s.mp)/100 * s.rev/10000000.0, 2)
            FROM seg s JOIN catavg c ON s.category = c.category
            ORDER BY (c.cmp - s.mp) DESC LIMIT 5""")
    ]
    return agg


def main() -> None:
    df = extract()
    print(f"extracted {len(df):,} rows")
    df = clean(df)
    print("cleaned + validated")

    con = sqlite3.connect(DB_PATH)
    try:
        load(df, con)
        print("loaded into SQLite")
        agg = aggregate(con)
        print("aggregated")
    finally:
        con.close()

    with open(AGG_PATH, "w") as f:
        json.dump(agg, f)
    k = agg["kpi"]
    print(f"done — revenue ₹{k['revenue']/1e7:,.2f} Cr, margin {k['margin_pct']}%, "
          f"{k['orders']:,} orders, {k['units']:,} units")


if __name__ == "__main__":
    main()
