"""
Phase 2 (Updated): SOH Calculation — รวม Qty in Transit
=========================================================
เพิ่มจาก version เดิม:
  1. โหลด Transfer Order → คำนวณ Qty in Transit per Item
     (Qty Shipped - Qty Received เฉพาะ row ที่ยัง pending)
  2. SOH NS side = On Hand + Qty in Transit
  3. On Floor Date fallback = Ship Date เก่าสุด (TO ใบแรก)
     จาก 000-คลังผลิต → ไม่ว่าจะไปที่ไหน

ประเด็นที่ clarify:
  - ไม่ยึด Item Category จาก Items Master
    → ยึด Location ใน NS/Erply เป็นตัวกรองแทน
  - Qty in Transit = Quantity Shipped - Quantity Received
    เฉพาะ Status: Pending Receipt, Pending Receipt/Partially Fulfilled
"""

import glob
import os
import pandas as pd

# ============================================================
# SECTION 1: Constants
# ============================================================

# 4 WH locations ฝั่ง NS ที่ SOW ให้ดึง (ยึด location เป็นหลัก)
NS_WH_LOCATIONS = [
    "FG Domestic Warehouse",
    "000-คลัง FG Sample",
    "000-คลัง FG Defect",
    "WH - Reserving",
]

# Status ที่ถือว่า "ยังอยู่ระหว่างขนส่ง"
TRANSIT_STATUSES = [
    "Pending Receipt",
    "Pending Receipt/Partially Fulfilled",
]

# Product Status ที่ตัดออกทั้ง NS และ Erply
EXCLUDE_PRODUCT_STATUS = ["PREMIUM", "SEMI"]

# Location Erply ที่ไม่นับ — ยึด NS เป็นหลักสำหรับ 4 WH locations
# Erply ใช้เฉพาะ location ที่ไม่ใช่ 4 WH นี้ (ตัวเลือก B)
ERPLY_EXCLUDE_LOCATIONS = [
    "FG Domestic Warehouse",
    "000-คลัง FG Sample",
    "000-คลัง FG Defect",
    "WH - Reserving",
]

# Regex สำหรับ validate SKU format ของ YMF
# Format: XX-XXXXXXX-XX-X-XXXX-X-XXXX (บางจุดมีหลักเพิ่มเติมได้)
#   Seg 1: 2 ตัวอักษรพิมพ์ใหญ่         เช่น PN, BL, DS, JK
#   Seg 2: 7 ตัว alphanumeric           เช่น 1032221, L220708 (อาจมีตัวอักษรนำ)
#   Seg 3: 1-2 ตัว alphanumeric         เช่น DP, NP, N
#   Seg 4: 1+ ตัว alphanumeric          เช่น N, L, M, S, 2, 3
#   Seg 5: 1+ ตัว alphanumeric          เช่น 0300, KAKI, Z605 (color code)
#   Seg 6: 1 ตัวอักษรพิมพ์ใหญ่         เช่น C, K, Z (brand code)
#   Seg 7: 4 ตัวเลข                     เช่น 2408, 2503 (ปี+เดือนที่ผลิต)
# รหัสที่ไม่ตรง format (เช่น OT-0002, SC-001, A-WHIT-...) จะถูกตัดออก
import re as _re
VALID_SKU_PATTERN = _re.compile(
    r'^[A-Z]{2}-[A-Z0-9]{7}-[A-Z0-9]{1,2}-[A-Z0-9]+-[A-Z0-9]+-[A-Z]-\d{4}$'
)

def is_valid_sku(code: str) -> bool:
    """คืน True ถ้า Item Code ตรงตาม SKU format มาตรฐานของ YMF"""
    if not isinstance(code, str):
        return False
    return bool(VALID_SKU_PATTERN.match(code.strip()))


# ============================================================
# SECTION 2: File Loaders
# ============================================================
import io

def _find_file(folder: str, prefix: str) -> str:
    for ext in ["*.xlsx", "*.xls"]:
        matches = glob.glob(os.path.join(folder, f"{prefix}{ext}"))
        if matches:
            return sorted(matches)[0]
    raise FileNotFoundError(f"ไม่พบไฟล์ '{prefix}*.xls(x)' ใน {folder}")

def _read_magic(path: str, skiprows: int = 0, dtype: dict = None) -> pd.DataFrame:
    """ฟังก์ชันฉลาด: ลองอ่าน .xlsx ก่อน -> ลอง .xls จริง -> ถ้าพังให้อ่านแบบ HTML (NetSuite)"""
    try:
        return pd.read_excel(path, skiprows=skiprows, engine="openpyxl", dtype=dtype)
    except Exception:
        pass
        
    try:
        return pd.read_excel(path, skiprows=skiprows, engine="xlrd", dtype=dtype)
    except Exception:
        pass

    print(f"  [Fallback] ไฟล์ {os.path.basename(path)} เป็น HTML ปลอมตัวมา กำลังแกะข้อมูล...")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html_content = f.read()
        
    dfs = pd.read_html(io.StringIO(html_content), extract_links=None)
    df = dfs[0]
    
    if skiprows > 0:
        df.columns = df.iloc[skiprows - 1] 
        df = df.iloc[skiprows:].reset_index(drop=True)
        df = df.loc[:, df.columns.notna()]
        
    if dtype:
        for col, t in dtype.items():
            if col in df.columns:
                df[col] = df[col].astype(t)
    return df

def load_items(folder: str) -> pd.DataFrame:
    path = _find_file(folder, "Items")
    df = _read_magic(path, skiprows=0, dtype={"Item Code": str, "Item Erply ID TH": str})
    df["Item Code"]           = df["Item Code"].str.strip()
    df["Active Sales Pricing"] = pd.to_numeric(df["Active Sales Pricing"], errors="coerce")
    df["Standard Cost"]        = pd.to_numeric(df["Standard Cost"],        errors="coerce")
    df["On Floor Date"]        = pd.to_datetime(df["On Floor Date"],        errors="coerce")
    df = df[df["Item Code"].apply(is_valid_sku)].copy()
    print(f"  [Items]         {len(df):,} rows | {os.path.basename(path)}")
    return df

def load_ns_onhand(folder: str) -> pd.DataFrame:
    path = _find_file(folder, "YMFINVENTORYONHANDREPORT")
    df = _read_magic(path, skiprows=5, dtype={"Item Code": str})
    df["Item Code"] = df["Item Code"].str.strip()
    df["On Hand"]   = pd.to_numeric(df["On Hand"], errors="coerce")
    df = df.dropna(subset=["Item Code"])
    df = df[df["Item Code"].apply(is_valid_sku)].copy()
    print(f"  [NS OnHand]     {len(df):,} rows | {os.path.basename(path)}")
    return df

def load_erply(folder: str) -> pd.DataFrame:
    path = _find_file(folder, "Inventory_By_Items")
    df = _read_magic(path, skiprows=0, dtype={"code": str})
    df = df.iloc[2:].reset_index(drop=True)
    df = df.rename(columns={
        "code":     "item_code",
        "location": "location",
        "available":"available",
        "lay-by":   "lay_by",
        "category": "category",
    })
    df["item_code"] = df["item_code"].astype(str).str.strip()
    df["available"] = pd.to_numeric(df["available"], errors="coerce").fillna(0)
    df["lay_by"]    = pd.to_numeric(df["lay_by"],    errors="coerce").fillna(0)
    df = df[df["item_code"].notna() & (df["item_code"] != "nan")].copy()
    df = df[df["item_code"].apply(is_valid_sku)].copy()
    print(f"  [Erply]         {len(df):,} rows | {os.path.basename(path)}")
    return df

def load_transfer_order(folder: str) -> pd.DataFrame:
    path = _find_file(folder, "YMFTRANSFERORDERBYITEMLISTResults")
    df = _read_magic(path, skiprows=0, dtype={"Item Number": str})
    df["Item Number"]       = df["Item Number"].str.strip()
    df["Ship Date"]         = pd.to_datetime(df["Ship Date"],         errors="coerce")
    df["Quantity Shipped"]  = pd.to_numeric(df["Quantity Shipped"],   errors="coerce").fillna(0)
    df["Quantity Received"] = pd.to_numeric(df["Quantity Received"],  errors="coerce").fillna(0)
    print(f"  [TransferOrder] {len(df):,} rows | {os.path.basename(path)}")
    return df

# ==========================================
# ⬇️ ก๊อปปี้ชุดนี้ไปวางแทรกตรงนี้ได้เลยครับ ⬇️
# ==========================================
def load_all_data(folder: str):
    """คืน tuple: (df_items, df_ns, df_erply, df_to)"""
    print("[load_all_data] กำลังโหลดไฟล์ทั้ง 4 ตัว...")
    df_items = load_items(folder)
    df_ns    = load_ns_onhand(folder)
    df_erply = load_erply(folder)
    df_to    = load_transfer_order(folder)
    print()
    return df_items, df_ns, df_erply, df_to

# ============================================================
# SECTION 3: Derive Qty in Transit per Item
# ============================================================

def get_qty_in_transit(df_to: pd.DataFrame) -> pd.DataFrame:
    """
    Qty in Transit = Quantity Shipped - Quantity Received
    เฉพาะ row ที่:
      - From Location คือ 000-คลังผลิต
      - Status ยัง pending (ยังไม่ถึงปลายทาง)
      - qty_in_transit > 0
    """
    mask = (
        df_to["From Location"].str.contains("คลังผลิต", na=False) &
        df_to["Status"].isin(TRANSIT_STATUSES)
    )
    df_t = df_to[mask].copy()
    df_t["qty_in_transit"] = df_t["Quantity Shipped"] - df_t["Quantity Received"]
    df_t = df_t[df_t["qty_in_transit"] > 0]

    result = (
        df_t.groupby("Item Number", as_index=False)["qty_in_transit"]
        .sum()
        .rename(columns={"Item Number": "item_code"})
    )
    print(f"[get_qty_in_transit]    Items ที่มี Qty in Transit: {len(result):,} | "
          f"Total qty: {result['qty_in_transit'].sum():,.0f}")
    return result


# ============================================================
# SECTION 4: Derive On Floor Date Fallback จาก Ship Date
# ============================================================

def get_floor_date_fallback(df_to: pd.DataFrame) -> pd.DataFrame:
    """
    On Floor Date fallback = Ship Date เก่าสุด (TO ใบแรก)
    ที่ออกจาก 000-คลังผลิต → ไม่ว่าจะไปที่ไหนก็ตาม
    หมายถึง: item ถูก launch จากโรงงานครั้งแรกวันนั้น
    """
    from_prod = df_to[
        df_to["From Location"].str.contains("คลังผลิต", na=False) &
        df_to["Ship Date"].notna()
    ].copy()

    result = (
        from_prod.groupby("Item Number", as_index=False)["Ship Date"]
        .min()   # เก่าสุด = TO ใบแรก = วัน launch จริง
        .rename(columns={"Item Number": "item_code",
                         "Ship Date":    "floor_date_fallback"})
    )
    print(f"[get_floor_date_fallback] Items ที่มี Ship Date fallback: {len(result):,}")
    return result


# ============================================================
# SECTION 5: SOH Calculation (รวม Qty in Transit)
# ============================================================

def calculate_soh(
    df_items:   pd.DataFrame,
    df_ns:      pd.DataFrame,
    df_erply:   pd.DataFrame,
    df_transit: pd.DataFrame,   # output จาก get_qty_in_transit()
) -> pd.DataFrame:
    """
    SOH per Item Code:
      NS side    = On Hand + Qty in Transit  (4 WH locations)
      Erply side = available + lay_by        (สาขา ไม่รวม FG WH)
    ตัด Product Status: PREMIUM, SEMI
    คืน DataFrame: item_code | on_hand | qty_in_transit | soh_ns | soh_erply | soh_total
    """

    # --- NS: filter 4 WH locations ---
    ns_wh = df_ns[df_ns["Location (Warehouse)"].isin(NS_WH_LOCATIONS)].copy()

    # Merge Product Status → filter Premium/Semi
    ns_wh = ns_wh.merge(df_items[["Item Code", "Product Status"]],
                        on="Item Code", how="left")
    ns_wh = ns_wh[~ns_wh["Product Status"].isin(EXCLUDE_PRODUCT_STATUS)]

    # Sum On Hand ต่อ Item
    soh_ns_base = (
        ns_wh.groupby("Item Code", as_index=False)["On Hand"]
        .sum()
        .rename(columns={"Item Code": "item_code", "On Hand": "on_hand"})
    )

    # Merge Qty in Transit → soh_ns = on_hand + transit
    soh_ns = soh_ns_base.merge(df_transit, on="item_code", how="left")
    soh_ns["qty_in_transit"] = soh_ns["qty_in_transit"].fillna(0)
    soh_ns["soh_ns"]         = soh_ns["on_hand"] + soh_ns["qty_in_transit"]
    soh_ns = soh_ns[["item_code", "on_hand", "qty_in_transit", "soh_ns"]]

    # --- Erply: filter สาขา (ไม่รวม FG Domestic WH) ---
    erply_branch = df_erply[
        ~df_erply["location"].isin(ERPLY_EXCLUDE_LOCATIONS)
    ].copy()

    # inner join → ตัด item ที่ไม่อยู่ใน Items Master ออก
    erply_branch = erply_branch.merge(
        df_items[["Item Code", "Product Status"]],
        left_on="item_code", right_on="Item Code", how="inner"
    )
    erply_branch = erply_branch[
        ~erply_branch["Product Status"].isin(EXCLUDE_PRODUCT_STATUS)
    ]
    erply_branch["qty_erply"] = erply_branch["available"] + erply_branch["lay_by"]

    soh_erply = (
        erply_branch.groupby("item_code", as_index=False)["qty_erply"]
        .sum()
        .rename(columns={"qty_erply": "soh_erply"})
    )

    # --- รวม NS + Erply ---
    df_soh = soh_ns.merge(soh_erply, on="item_code", how="outer")
    for col in ["on_hand", "qty_in_transit", "soh_ns", "soh_erply"]:
        df_soh[col] = df_soh[col].fillna(0)
    df_soh["soh_total"] = df_soh["soh_ns"] + df_soh["soh_erply"]

    # ตัด item ที่ soh_total = 0
    df_soh = df_soh[df_soh["soh_total"] > 0].copy()

    print(
        f"[calculate_soh]         Items มียอด SOH: {len(df_soh):,} | "
        f"On Hand={df_soh['on_hand'].sum():,.0f} | "
        f"In Transit={df_soh['qty_in_transit'].sum():,.0f} | "
        f"NS={df_soh['soh_ns'].sum():,.0f} | "
        f"Erply={df_soh['soh_erply'].sum():,.0f} | "
        f"Total={df_soh['soh_total'].sum():,.0f}"
    )
    return df_soh


# ============================================================
# SECTION 6: Main — ทดสอบ
# ============================================================
if __name__ == "__main__":
    DATA_FOLDER = "data_cache" 

    print("=" * 58)
    print("  PHASE 2 (Updated): SOH + Qty in Transit")
    print("=" * 58 + "\n")
    
    # ผมใส่เครื่องหมาย # ปิดบรรทัดที่สั่งรันข้อมูลข้างล่างนี้ทั้งหมดแล้วนะครับ
    # df_items, df_ns, df_erply, df_to = load_all_data(DATA_FOLDER)
    # df_transit  = get_qty_in_transit(df_to)
    # df_fallback = get_floor_date_fallback(df_to)
    # df_soh      = calculate_soh(df_items, df_ns, df_erply, df_transit)

    # print(f"\nItems ที่มี Qty in Transit > 0:")
    # print(df_soh[df_soh["qty_in_transit"] > 0].head(8).to_string(index=False))

    # print(f"\nSample On Floor Date fallback (5 rows):")
    # print(df_fallback.head(5).to_string(index=False))

    print("\n[READY] Phase 2 is ready for Streamlit Cloud")