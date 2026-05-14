"""
Phase 1: Data Loading & Inspection
====================================
โหลดไฟล์ Raw Data ทั้ง 3 ตัวเข้า DataFrame
พร้อมแสดงข้อมูลสำคัญเพื่อตรวจสอบ Key Columns ก่อน Merge

วิธีใช้: วางไฟล์ xlsx ทั้ง 3 ตัวไว้ใน folder เดียวกับ script นี้
จากนั้นรัน: python phase1_load_inspect.py
"""

import glob
import os
import pandas as pd

# ======================================================
# SECTION 1: Helper Function สำหรับหาไฟล์จากชื่อขึ้นต้น
# ======================================================
def find_file(folder: str, prefix: str) -> str:
    """
    ค้นหาไฟล์ .xlsx ที่ขึ้นต้นด้วย prefix ที่กำหนด
    ป้องกัน error กรณีชื่อไฟล์มีตัวเลขสุ่มต่อท้ายเสมอ
    """
    pattern = os.path.join(folder, f"{prefix}*.xlsx")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            f"ไม่พบไฟล์ที่ขึ้นต้นด้วย '{prefix}' ใน folder: {folder}\n"
            f"Pattern ที่ใช้ค้นหา: {pattern}"
        )
    if len(matches) > 1:
        print(f"  [WARNING] พบไฟล์ {len(matches)} ตัวที่ match '{prefix}*' → ใช้ตัวแรก: {matches[0]}")
    return matches[0]


# ======================================================
# SECTION 2: Config — แก้ DATA_FOLDER ให้ตรงกับ path จริง
# ======================================================
DATA_FOLDER = "."   # "." = folder เดียวกับ script, หรือใส่ path เต็ม เช่น r"C:\Users\you\data"

FILE_PREFIXES = {
    "items":    "Items",                       # Item Master จาก NetSuite
    "ns_onhand": "YMFINVENTORYONHANDREPORT",   # Inventory On Hand จาก NetSuite
    "erply":    "Inventory_By_Items",          # Inventory จาก Erply
}


# ======================================================
# SECTION 3: โหลด Items (Item Master) — ไม่มี header ขยะ
# ======================================================
def load_items(folder: str) -> pd.DataFrame:
    path = find_file(folder, FILE_PREFIXES["items"])
    print(f"\n[1] โหลด Items Master: {os.path.basename(path)}")

    df = pd.read_excel(
        path,
        engine="openpyxl",   # รองรับ xlsx และ encoding ภาษาไทยได้ถูกต้อง
        dtype={
            "Item Code": str,         # ป้องกัน Pandas แปลง Item Code เป็น float
            "Item Erply ID TH": str,  # Erply ID อาจมี leading zero
        }
    )
    # strip whitespace ออกจาก Item Code เผื่อกรณีมี space ต่อท้าย
    df["Item Code"] = df["Item Code"].str.strip()
    return df


# ======================================================
# SECTION 4: โหลด NS On Hand — มี header ขยะ 5 แถวบนสุด
# ======================================================
# โครงสร้างไฟล์จริง:
#   แถว 0: "YMF International Thai Co., Ltd."  ← ขยะ
#   แถว 1: "YMF INVENTORY ON HAND REPORT..."   ← ขยะ
#   แถว 2: "As of 02 February 2026"            ← ขยะ
#   แถว 3-4: blank row                         ← ขยะ
#   แถว 5: Header จริง (Location, Item Code, On Hand ...)
# ดังนั้น skiprows=5
def load_ns_onhand(folder: str) -> pd.DataFrame:
    path = find_file(folder, FILE_PREFIXES["ns_onhand"])
    print(f"\n[2] โหลด NS On Hand: {os.path.basename(path)}")

    df = pd.read_excel(
        path,
        skiprows=5,      # ข้าม header ขยะ 5 แถวแรก
        engine="openpyxl",
        dtype={"Item Code": str},
    )
    df["Item Code"] = df["Item Code"].str.strip()

    # แปลง On Hand เป็น numeric (บางแถวอาจเป็น string จาก subtotal)
    df["On Hand"] = pd.to_numeric(df["On Hand"], errors="coerce")

    # ลบแถว subtotal/grand total ที่ Item Code เป็น NaN
    df = df.dropna(subset=["Item Code"])
    return df


# ======================================================
# SECTION 5: โหลด Erply — มี sub-header ขยะ 2 แถว (แถว 0-1)
# ======================================================
# โครงสร้างไฟล์จริง:
#   แถว 0: column header หลัก (code, EAN code, product ...)
#   แถว 1: sub-header (purchase price, total, warehouse price ...)  ← ขยะ
#   แถว 2: sub-header ต่อเนื่อง                                    ← ขยะ
#   แถว 3+: ข้อมูลจริง
# วิธีแก้: โหลด skiprows=0 เพื่อเอา column name จาก row แรก
#          แล้ว drop 2 แถวแรกของข้อมูลที่เป็น sub-header ออก
def load_erply(folder: str) -> pd.DataFrame:
    path = find_file(folder, FILE_PREFIXES["erply"])
    print(f"\n[3] โหลด Erply Inventory: {os.path.basename(path)}")

    df = pd.read_excel(
        path,
        skiprows=0,      # เอา row 0 เป็น header (ชื่อคอลัมน์หลัก)
        engine="openpyxl",
        dtype={
            "code": str,      # Item Code (SKU) ใน Erply
            "EAN code": str,
        }
    )

    # Drop 2 แถวแรกที่เป็น sub-header (index 0 และ 1)
    df = df.iloc[2:].reset_index(drop=True)

    # Rename columns ให้ชัดเจน (แก้ชื่อซ้ำ average / average.1)
    df = df.rename(columns={
        "Unnamed: 0":   "no",
        "code":         "item_code",        # KEY สำหรับ Merge กับ Items
        "EAN code":     "ean_code",
        "product":      "product_name",
        "category":     "category",
        "location":     "location",
        "available":    "available",
        "lay-by":       "lay_by",
        "On Order":     "on_order",
        "In Transfer":  "in_transfer",
        "unit":         "unit",
        "average":      "avg_purchase_price",
        "purchase":     "total_purchase",
        "average.1":    "avg_warehouse_price",
        "warehouse":    "total_warehouse",
        "sales price":  "sales_price",
        "total":        "total_in_sales_prices",
        "stock days":   "stock_days",
    })

    df["item_code"] = df["item_code"].astype(str).str.strip()

    # แปลง numeric columns
    for col in ["available", "lay_by", "sales_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ลบแถวที่ item_code เป็น nan หรือ "nan" (แถว subtotal)
    df = df[df["item_code"].notna() & (df["item_code"] != "nan")]
    return df


# ======================================================
# SECTION 6: Inspection — แสดงผลเพื่อตรวจสอบ
# ======================================================
def inspect(label: str, df: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Shape      : {df.shape[0]:,} rows × {df.shape[1]} columns")
    print(f"\n  Columns    :\n  {list(df.columns)}")
    print(f"\n  Head(3)    :")
    print(df.head(3).to_string())
    print()


# ======================================================
# SECTION 7: Main
# ======================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  PHASE 1: Data Loading & Inspection")
    print("=" * 60)

    # โหลดไฟล์ทั้ง 3
    df_items    = load_items(DATA_FOLDER)
    df_ns       = load_ns_onhand(DATA_FOLDER)
    df_erply    = load_erply(DATA_FOLDER)

    # แสดงผล
    inspect("1. Items Master (NetSuite)", df_items)
    inspect("2. NS On Hand Report", df_ns)
    inspect("3. Erply Inventory By Items", df_erply)

    # ======================================================
    # SECTION 8: Key Column Check — ตรวจสอบ Key สำหรับ Merge
    # ======================================================
    print("=" * 60)
    print("  KEY COLUMN CHECK (สำหรับ Merge)")
    print("=" * 60)

    # Items Key
    items_key_col = "Item Code"
    items_key_nulls = df_items[items_key_col].isna().sum()
    print(f"\n[Items]   Key='{items_key_col}' | Nulls={items_key_nulls} | Unique={df_items[items_key_col].nunique():,}")
    print(f"          ตัวอย่าง: {df_items[items_key_col].dropna().head(3).tolist()}")

    # NS On Hand Key
    ns_key_col = "Item Code"
    ns_key_nulls = df_ns[ns_key_col].isna().sum()
    print(f"\n[NS]      Key='{ns_key_col}' | Nulls={ns_key_nulls} | Unique={df_ns[ns_key_col].nunique():,}")
    print(f"          ตัวอย่าง: {df_ns[ns_key_col].dropna().head(3).tolist()}")

    # Erply Key
    erply_key_col = "item_code"
    erply_key_nulls = df_erply[erply_key_col].isna().sum()
    print(f"\n[Erply]   Key='{erply_key_col}' | Nulls={erply_key_nulls} | Unique={df_erply[erply_key_col].nunique():,}")
    print(f"          ตัวอย่าง: {df_erply[erply_key_col].dropna().head(3).tolist()}")

    # Cross-check: Items ที่อยู่ใน Erply แต่ไม่มีใน Items Master
    erply_codes = set(df_erply["item_code"].dropna())
    items_codes = set(df_items["Item Code"].dropna())
    not_in_items = erply_codes - items_codes
    print(f"\n[Cross-check] Item codes ที่อยู่ใน Erply แต่ไม่พบใน Items Master: {len(not_in_items):,} รายการ")
    if not_in_items:
        print(f"  ตัวอย่าง 5 รายการแรก: {list(not_in_items)[:5]}")

    ns_codes = set(df_ns["Item Code"].dropna())
    not_in_items_ns = ns_codes - items_codes
    print(f"\n[Cross-check] Item codes ที่อยู่ใน NS แต่ไม่พบใน Items Master  : {len(not_in_items_ns):,} รายการ")
    if not_in_items_ns:
        print(f"  ตัวอย่าง 5 รายการแรก: {list(not_in_items_ns)[:5]}")

    print("\n[DONE] Phase 1 เสร็จสมบูรณ์ — พร้อมเข้า Phase 2: SOH Calculation")
