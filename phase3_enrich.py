"""
Phase 3 (Updated): Build Enriched Dataset
==========================================
เปลี่ยนแปลงจาก version เดิม:
  1. ไม่ filter ด้วย Item Category จาก Items Master อีกต่อไป
     → ยึด Location ใน NS/Erply เป็นตัวกรองแทน (ตามที่ clarify)
  2. On Floor Date fallback: ถ้า Item Master ไม่มีวันที่
     → ใช้ Ship Date เก่าสุดจาก Transfer Order แทน
  3. enriched dataset มี column: effective_floor_date
     ที่รวม On Floor Date จริง + fallback ไว้แล้วในคอลัมน์เดียว

วิธีใช้: python phase3_enrich.py
หรือ import: from phase3_enrich import build_enriched_dataset, build_soh_by_location
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase2_soh import (
    load_all_data,
    get_qty_in_transit,
    get_floor_date_fallback,
    calculate_soh,
    EXCLUDE_PRODUCT_STATUS,
    NS_WH_LOCATIONS,
    ERPLY_EXCLUDE_LOCATIONS,
)

# ============================================================
# SECTION 1: Columns จาก Items Master ที่ใช้ในรายงาน
# ============================================================
ITEMS_COLS_KEEP = [
    "Item Code",
    "Item Name",
    "Product Status",
    "Price Status",
    "Production Type",
    "Product Number Reference",   # Style#
    "Product Type",
    "Fabric Main",
    "Data Color",
    "Color Name",
    "Size",
    "Season for MPS",
    "Standard Cost",
    "Active Sales Pricing",
    "On Floor Date",              # อาจเป็น NaT → ใช้ fallback แทน
    "Class",
    "Inactive",
]


# ============================================================
# SECTION 2: Build Enriched Dataset
# ============================================================

def build_enriched_dataset(
    df_items:   pd.DataFrame,
    df_soh:     pd.DataFrame,
    df_fallback: pd.DataFrame,   # output จาก get_floor_date_fallback()
) -> pd.DataFrame:
    """
    Merge SOH + On Floor Date fallback กลับเข้า Items Master

    Steps:
      A) Filter Items Master: ไม่ใช่ Premium/Semi, Active item เท่านั้น
         (ไม่ filter ด้วย Category แล้ว — ยึด location จาก NS/Erply)
      B) Left join กับ df_soh
      C) Merge On Floor Date fallback → สร้าง effective_floor_date
      D) คำนวณ flag: has_price, has_floor_date, amt_total
      E) ตัด item ที่ soh_total = 0
    """

    # --- STEP A: Filter Items Master ---
    df_base = df_items[
        ~df_items["Product Status"].isin(EXCLUDE_PRODUCT_STATUS) &
        (df_items["Inactive"] == "No")
    ][ITEMS_COLS_KEEP].copy()

    print(f"[build_enriched] Items หลัง filter (Active, ไม่ใช่ Premium/Semi): {len(df_base):,}")

    # --- STEP B: Merge SOH ---
    df = df_base.merge(
        df_soh,
        left_on="Item Code", right_on="item_code",
        how="left"
    )
    df = df.drop(columns=["item_code"], errors="ignore")

    for col in ["on_hand", "qty_in_transit", "soh_ns", "soh_erply", "soh_total"]:
        df[col] = df[col].fillna(0)

    # --- STEP C: Effective Floor Date = On Floor Date จริง, ถ้าไม่มีใช้ fallback ---
    df = df.merge(df_fallback, left_on="Item Code", right_on="item_code", how="left")
    df = df.drop(columns=["item_code"], errors="ignore")

    # สร้าง effective_floor_date: ใช้ On Floor Date จริงก่อน ถ้า NaT → ใช้ fallback
    df["effective_floor_date"] = df["On Floor Date"].combine_first(
        df["floor_date_fallback"]
    )

    # flag ว่า floor date มาจากแหล่งไหน (สำหรับ audit/debug)
    df["floor_date_source"] = "items_master"
    mask_fallback = df["On Floor Date"].isna() & df["floor_date_fallback"].notna()
    df.loc[mask_fallback, "floor_date_source"] = "transfer_order_fallback"
    mask_missing  = df["effective_floor_date"].isna()
    df.loc[mask_missing,  "floor_date_source"] = "missing"

    # --- STEP D: คำนวณ Flag & Amt. ---
    df["has_price"] = (
        df["Active Sales Pricing"].notna() & (df["Active Sales Pricing"] > 0)
    )
    df["has_floor_date"] = df["effective_floor_date"].notna()

    # Amt. = soh_total × Active Sales Pricing; ถ้าไม่มีราคา → None (= "ERROR" ตอน export)
    df["amt_total"] = df.apply(
        lambda r: r["soh_total"] * r["Active Sales Pricing"] if r["has_price"] else None,
        axis=1
    )

    # --- STEP E: ตัด soh_total = 0 ---
    df_out = df[df["soh_total"] > 0].copy()

    # สรุปผล
    n_fallback = (df_out["floor_date_source"] == "transfer_order_fallback").sum()
    n_missing  = (df_out["floor_date_source"] == "missing").sum()
    print(f"[build_enriched] Items มี SOH > 0         : {len(df_out):,}")
    print(f"[build_enriched] Floor date — Items Master : {(df_out['floor_date_source']=='items_master').sum():,}")
    print(f"[build_enriched] Floor date — TO Fallback  : {n_fallback:,}")
    print(f"[build_enriched] Floor date — ยังไม่มีเลย : {n_missing:,}  ← จะแสดง ERROR ใน Aging Report")
    print(f"[build_enriched] ไม่มีราคา (Amt. ERROR)   : {(~df_out['has_price']).sum():,}")

    return df_out


# ============================================================
# SECTION 3: SOH แยก per Location (สำหรับ Aging Report sort)
# ============================================================

def build_soh_by_location(
    df_items: pd.DataFrame,
    df_ns:    pd.DataFrame,
    df_erply: pd.DataFrame,
) -> pd.DataFrame:
    """
    SOH แยกทุก Location (ไม่ group รวม)
    Aging Report ต้องการ sort by Location ตาม SOW

    คืน: item_code | location | source | qty
    """
    # --- NS side ---
    ns_wh = df_ns[df_ns["Location (Warehouse)"].isin(NS_WH_LOCATIONS)].copy()
    ns_wh = ns_wh.merge(df_items[["Item Code", "Product Status"]],
                        on="Item Code", how="left")
    ns_wh = ns_wh[~ns_wh["Product Status"].isin(EXCLUDE_PRODUCT_STATUS)]

    ns_loc = ns_wh[["Item Code", "Location (Warehouse)", "On Hand"]].rename(columns={
        "Item Code":            "item_code",
        "Location (Warehouse)": "location",
        "On Hand":              "qty",
    })
    ns_loc["source"] = "NS"

    # --- Erply side ---
    erply_branch = df_erply[
        ~df_erply["location"].isin(ERPLY_EXCLUDE_LOCATIONS)
    ].copy()
    erply_branch = erply_branch.merge(
        df_items[["Item Code", "Product Status"]],
        left_on="item_code", right_on="Item Code", how="inner"
    )
    erply_branch = erply_branch[
        ~erply_branch["Product Status"].isin(EXCLUDE_PRODUCT_STATUS)
    ]
    erply_branch["qty"] = erply_branch["available"] + erply_branch["lay_by"]

    erply_loc = erply_branch[["item_code", "location", "qty"]].copy()
    erply_loc["source"] = "Erply"

    df_loc = pd.concat([ns_loc, erply_loc], ignore_index=True)
    df_loc = df_loc[df_loc["qty"] > 0].copy()

    print(f"[build_soh_by_location] Rows (item × location): {len(df_loc):,}")
    return df_loc


# ============================================================
# SECTION 4: Sanity Check
# ============================================================

def sanity_check(df_enriched: pd.DataFrame, df_soh: pd.DataFrame):
    print("\n" + "=" * 58)
    print("  SANITY CHECK — Phase 3")
    print("=" * 58)

    p2_total = df_soh["soh_total"].sum()
    p3_total = df_enriched["soh_total"].sum()
    diff     = abs(p2_total - p3_total)
    pct      = diff / p2_total * 100 if p2_total else 0

    print(f"SOH total Phase 2 : {p2_total:,.0f}")
    print(f"SOH total Phase 3 : {p3_total:,.0f}")
    print(f"Difference        : {diff:,.0f}  ({pct:.2f}%)")
    status = "[OK]" if pct < 5 else "[WARNING] ต่างกันเกิน 5% — ตรวจสอบ filter"
    print(f"Status            : {status}")

    print(f"\nTop 5 items by SOH:")
    cols = ["Item Code","Item Name","soh_ns","soh_erply","soh_total",
            "effective_floor_date","floor_date_source","has_price"]
    print(df_enriched.nlargest(5, "soh_total")[cols].to_string(index=False))


# ============================================================
# SECTION 5: Main
# ============================================================
if __name__ == "__main__":
    DATA_FOLDER = "."

    print("=" * 58)
    print("  PHASE 3 (Updated): Build Enriched Dataset")
    print("=" * 58 + "\n")

    df_items, df_ns, df_erply, df_to = load_all_data(DATA_FOLDER)
    df_transit  = get_qty_in_transit(df_to)
    df_fallback = get_floor_date_fallback(df_to)
    df_soh      = calculate_soh(df_items, df_ns, df_erply, df_transit)

    df_enriched  = build_enriched_dataset(df_items, df_soh, df_fallback)
    df_soh_loc   = build_soh_by_location(df_items, df_ns, df_erply)

    sanity_check(df_enriched, df_soh)

    print(f"\nFloor date source breakdown:\n{df_enriched['floor_date_source'].value_counts().to_string()}")

    print(f"\nLocation summary (SOH by location):")
    print(
        df_soh_loc.groupby(["source","location"])["qty"]
        .sum().sort_values(ascending=False).head(15).to_string()
    )

    print("\n[DONE] Phase 3 Updated เสร็จ — พร้อม Phase 4: Inventory Aging Report")
