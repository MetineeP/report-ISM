"""
app.py — YMF Self-Service Report Portal
=========================================
Streamlit web app สำหรับทีม MER รัน 3 รายงานด้วยตัวเอง

วิธีรัน (local):
  streamlit run app.py

วิธี deploy (Streamlit Cloud):
  push ขึ้น GitHub แล้ว connect ที่ share.streamlit.io
  ใส่ secrets ใน Streamlit Cloud settings

Secrets ที่ต้องตั้งใน Streamlit Cloud (.streamlit/secrets.toml):
  GDRIVE_RAW_DATA_FOLDER_ID = "1f1ids90cV_CmqSRyWgUEhR9epVed87sG"

  [gcp_service_account]
  type = "service_account"
  project_id = "..."
  private_key_id = "..."
  private_key = "-----BEGIN RSA PRIVATE KEY-----\n..."
  client_email = "report-ism-reader@report-ism.iam.gserviceaccount.com"
  client_id = "..."
  auth_uri = "https://accounts.google.com/o/oauth2/auth"
  token_uri = "https://oauth2.googleapis.com/token"
"""

import io
import os
import sys
import json
import tempfile
import subprocess
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account

# ============================================================
# SECTION 1: Page Config
# ============================================================
st.set_page_config(
    page_title="YMF Report Portal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# SECTION 2: Custom CSS
# ============================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@300;400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans Thai', sans-serif; }
    #MainMenu, footer, header { visibility: hidden; }
    .stApp { background-color: #F4F6F9; }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0F2744 0%, #1B3F6B 100%);
    }
    section[data-testid="stSidebar"] * { color: #E2EAF4 !important; }
    section[data-testid="stSidebar"] hr { border-color: #2D527A; }

    /* KPI Card */
    .kpi-card {
        background: white; border-radius: 10px;
        padding: 18px 20px; margin-bottom: 10px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        border-left: 4px solid #1F4E79;
    }
    .kpi-card.red    { border-left-color: #C00000; }
    .kpi-card.green  { border-left-color: #375623; }
    .kpi-card.amber  { border-left-color: #ED7D31; }
    .kpi-card.purple { border-left-color: #4B3E6B; }
    .kpi-label { font-size: 0.72rem; color: #6B7280; font-weight: 600;
                 text-transform: uppercase; letter-spacing: 0.06em; }
    .kpi-value { font-size: 1.7rem; font-weight: 600; color: #111827; line-height: 1.2; margin: 4px 0; }
    .kpi-sub   { font-size: 0.72rem; color: #9CA3AF; }

    /* Status banners */
    .banner-warn    { background:#FFF7ED; border:1px solid #FED7AA; border-radius:8px;
                      padding:10px 14px; font-size:0.83rem; color:#92400E; margin-bottom:10px; }
    .banner-error   { background:#FEF2F2; border:1px solid #FECACA; border-radius:8px;
                      padding:10px 14px; font-size:0.83rem; color:#991B1B; margin-bottom:10px; }
    .banner-success { background:#F0FDF4; border:1px solid #BBF7D0; border-radius:8px;
                      padding:10px 14px; font-size:0.83rem; color:#166534; margin-bottom:10px; }

    /* Section header */
    .section-hdr { font-size:0.8rem; font-weight:600; color:#374151;
                   text-transform:uppercase; letter-spacing:0.08em;
                   border-bottom:2px solid #E5E7EB; padding-bottom:6px; margin:16px 0 10px; }

    /* File uploader ใน sidebar — แก้สีชื่อไฟล์ให้ชัด */
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] {
        background: #1A3A5C !important;
        border: 1.5px dashed #7AAFD4 !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] * {
        color: #FFFFFF !important;
    }
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] button {
        background: #2D6EA8 !important;
        border: 1px solid #7AAFD4 !important;
        color: #FFFFFF !important;
        border-radius: 6px !important;
    }
    /* ชื่อไฟล์ที่ upload แล้ว */
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] p,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] small,
    section[data-testid="stSidebar"] [data-testid="stFileUploaderFileName"] {
        color: #FFFFFF !important;
        font-weight: 500 !important;
    }

    /* Checkbox และ status ไฟล์ใน sidebar */
    section[data-testid="stSidebar"] .stMarkdown p {
        color: #E2EAF4 !important;
        font-size: 0.85rem !important;
        margin: 2px 0 !important;
    }
    section[data-testid="stSidebar"] input[type="text"],
    section[data-testid="stSidebar"] input[type="date"],
    section[data-testid="stSidebar"] .stDateInput input {
        color: #FFFFFF !important;
        background-color: #2D527A !important;
        border: 1px solid #4A7BA8 !important;
        border-radius: 6px !important;
    }
    section[data-testid="stSidebar"] .stDateInput input::placeholder {
        color: #A8C4E0 !important;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# SECTION 3: Google Drive Helper
# ============================================================

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

@st.cache_resource
def get_drive_service():
    """สร้าง Google Drive service จาก Streamlit secrets (optional)"""
    try:
        if "gcp_service_account" not in st.secrets:
            return None
        creds_info = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def list_files_in_folder(service, folder_id: str) -> list[dict]:
    """คืน list ของไฟล์ใน folder"""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, modifiedTime)",
        orderBy="name"
    ).execute()
    return results.get("files", [])


def download_file_to_buffer(service, file_id: str) -> io.BytesIO:
    """ดาวน์โหลดไฟล์จาก Drive → BytesIO buffer พร้อม retry 3 ครั้ง"""
    for attempt in range(3):
        try:
            request    = service.files().get_media(fileId=file_id)
            buffer     = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request, chunksize=10*1024*1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            return buffer
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"ดาวน์โหลดไฟล์ไม่สำเร็จหลังลอง 3 ครั้ง: {e}")
            import time
            time.sleep(2)


def find_file_buffer(service, files: list[dict], prefix: str) -> Optional[io.BytesIO]:
    """หาไฟล์ที่ขึ้นต้นด้วย prefix (case-insensitive) แล้ว download"""
    prefix_lower = prefix.lower()
    matches = [f for f in files if f["name"].lower().startswith(prefix_lower)]
    if not matches:
        return None
    target = sorted(matches, key=lambda x: x["name"])[0]
    return download_file_to_buffer(service, target["id"])


# ============================================================
# SECTION 4: Load Data from Google Drive
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)   # cache 5 นาที
def load_raw_data_from_drive(folder_id: str) -> dict:
    """
    โหลดไฟล์ทั้งหมดจาก raw_data folder ใน Google Drive
    คืน dict ของ DataFrames พร้อมใช้งาน
    """
    service = get_drive_service()
    if not service:
        return {}

    files = list_files_in_folder(service, folder_id)
    if not files:
        st.error("❌ ไม่พบไฟล์ใน raw_data folder — กรุณาตรวจสอบว่าวางไฟล์ถูก folder")
        return {}

    file_names = [f["name"] for f in files]

    # --- โหลดทีละไฟล์ ---
    buffers = {}
    prefixes = {
        "items":    "Items",
        "ns":       "YMFINVENTORYONHANDREPORT",
        "erply":    "Inventory By Items",
        "transfer": "YMFTRANSFERORDERBYITEMLISTResults",
        "sales":    "YMFSALESDATAWITHCOSTResults",
    }

    missing = []
    for key, prefix in prefixes.items():
        buf = find_file_buffer(service, files, prefix)
        if buf is None:
            missing.append(prefix)
        else:
            buffers[key] = buf

    if missing:
        st.warning(f"⚠️ ไม่พบไฟล์: {', '.join(missing)}")

    return buffers, file_names


# ============================================================
# SECTION 5: Process Reports (เรียก Phase 2-6)
# ============================================================

def run_aging_report(buffers: dict, as_of_date: date) -> tuple:
    """รัน Inventory Aging Report"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from phase2_soh import (
        load_all_data, get_qty_in_transit, get_floor_date_fallback,
        calculate_soh, load_items, load_ns_onhand, load_erply, load_transfer_order
    )
    from phase3_enrich import build_enriched_dataset, build_soh_by_location
    from phase4_aging import calculate_aging, build_summary, export_aging_excel

    # โหลดจาก buffer แทน folder
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()

    try:
        # เขียน buffer ลง temp files
        file_map = {
            "Items_tmp.xlsx":                            buffers.get("items"),
            "YMFINVENTORYONHANDREPORT_tmp.xlsx":         buffers.get("ns"),
            "Inventory_By_Items_tmp.xlsx":               buffers.get("erply"),
            "YMFTRANSFERORDERBYITEMLISTResults_tmp.xlsx": buffers.get("transfer"),
        }
        for fname, buf in file_map.items():
            if buf:
                buf.seek(0)
                with open(os.path.join(tmpdir, fname), "wb") as f:
                    f.write(buf.read())

        df_items   = load_items(tmpdir)
        df_ns      = load_ns_onhand(tmpdir)
        df_erply   = load_erply(tmpdir)
        df_to      = load_transfer_order(tmpdir)
        df_transit  = get_qty_in_transit(df_to)
        df_fallback = get_floor_date_fallback(df_to)
        df_soh      = calculate_soh(df_items, df_ns, df_erply, df_transit)
        df_enriched = build_enriched_dataset(df_items, df_soh, df_fallback)
        df_soh_loc  = build_soh_by_location(df_items, df_ns, df_erply)
        df_report   = calculate_aging(df_enriched, df_soh_loc, df_items, df_ns, as_of_date)
        summary     = build_summary(df_report, as_of_date)
        return df_report, summary

    finally:
        shutil.rmtree(tmpdir)


def run_sellthrough_report(buffers: dict, date_from: date, date_to: date) -> tuple:
    """รัน Sell-through Report"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from phase2_soh import (
        get_qty_in_transit, get_floor_date_fallback, calculate_soh,
        load_items, load_ns_onhand, load_erply, load_transfer_order
    )
    from phase3_enrich import build_enriched_dataset, build_soh_by_location
    from phase5_sellthrough import (
        calculate_sellthrough, build_sellthrough_summary, load_sales_data
    )

    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()

    try:
        file_map = {
            "Items_tmp.xlsx":                               buffers.get("items"),
            "YMFINVENTORYONHANDREPORT_tmp.xlsx":            buffers.get("ns"),
            "Inventory_By_Items_tmp.xlsx":                  buffers.get("erply"),
            "YMFTRANSFERORDERBYITEMLISTResults_tmp.xlsx":  buffers.get("transfer"),
            "YMFSALESDATAWITHCOSTResults_tmp.xlsx":        buffers.get("sales"),
        }
        for fname, buf in file_map.items():
            if buf:
                buf.seek(0)
                with open(os.path.join(tmpdir, fname), "wb") as f:
                    f.write(buf.read())

        df_items    = load_items(tmpdir)
        df_ns       = load_ns_onhand(tmpdir)
        df_erply    = load_erply(tmpdir)
        df_to       = load_transfer_order(tmpdir)
        df_transit  = get_qty_in_transit(df_to)
        df_fallback = get_floor_date_fallback(df_to)
        # ส่ง buffer ตรงๆ ไม่ต้องผ่าน LibreOffice
        sales_buf = buffers.get("sales")
        df_sales    = load_sales_data(buffer=sales_buf)
        df_report   = calculate_sellthrough(
            df_items, df_ns, df_erply, df_to,
            df_transit, df_fallback, df_sales,
            date_from, date_to
        )
        summary = build_sellthrough_summary(df_report, date_from, date_to)
        return df_report, summary

    finally:
        shutil.rmtree(tmpdir)


def run_accumulated_report(buffers: dict, date_from: date, date_to: date) -> tuple:
    """รัน Accumulated Sales Report"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from phase2_soh import (
        get_qty_in_transit, get_floor_date_fallback, calculate_soh,
        load_items, load_ns_onhand, load_erply, load_transfer_order
    )
    from phase3_enrich import build_enriched_dataset, build_soh_by_location
    from phase5_sellthrough import load_sales_data
    from phase6_accumulated import calculate_accumulated, build_accumulated_summary

    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()

    try:
        file_map = {
            "Items_tmp.xlsx":                               buffers.get("items"),
            "YMFINVENTORYONHANDREPORT_tmp.xlsx":            buffers.get("ns"),
            "Inventory_By_Items_tmp.xlsx":                  buffers.get("erply"),
            "YMFTRANSFERORDERBYITEMLISTResults_tmp.xlsx":  buffers.get("transfer"),
        }
        for fname, buf in file_map.items():
            if buf:
                buf.seek(0)
                with open(os.path.join(tmpdir, fname), "wb") as f:
                    f.write(buf.read())

        df_items    = load_items(tmpdir)
        df_ns       = load_ns_onhand(tmpdir)
        df_erply    = load_erply(tmpdir)
        df_to       = load_transfer_order(tmpdir)
        df_transit  = get_qty_in_transit(df_to)
        df_fallback = get_floor_date_fallback(df_to)
        # ส่ง buffer ตรงๆ ไม่ต้องผ่าน LibreOffice
        sales_buf = buffers.get("sales")
        df_sales    = load_sales_data(buffer=sales_buf)
        df_report   = calculate_accumulated(
            df_items, df_ns, df_erply,
            df_transit, df_fallback, df_sales,
            date_from, date_to
        )
        summary = build_accumulated_summary(df_report, date_from, date_to)
        return df_report, summary

    finally:
        shutil.rmtree(tmpdir)


# ============================================================
# SECTION 6: Excel Export to BytesIO (สำหรับ Download button)
# ============================================================

def export_to_bytes(df_report, report_type: str, **kwargs) -> bytes:
    """Export report เป็น Excel bytes สำหรับ st.download_button"""
    import tempfile, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    with tempfile.TemporaryDirectory() as tmpdir:
        if report_type == "aging":
            from phase4_aging import export_aging_excel
            fpath = export_aging_excel(df_report, kwargs["as_of_date"], tmpdir)
        elif report_type == "sellthrough":
            from phase5_sellthrough import export_sellthrough_excel
            fpath = export_sellthrough_excel(df_report, kwargs["date_from"], kwargs["date_to"], tmpdir)
        elif report_type == "accumulated":
            from phase6_accumulated import export_accumulated_excel
            fpath = export_accumulated_excel(df_report, kwargs["date_from"], kwargs["date_to"], tmpdir)

        with open(fpath, "rb") as f:
            return f.read()


# ============================================================
# SECTION 7: UI Components
# ============================================================

def kpi_card(label: str, value: str, sub: str = "", color: str = "blue"):
    color_map = {"blue": "", "red": " red", "green": " green", "amber": " amber", "purple": " purple"}
    cls = color_map.get(color, "")
    st.markdown(f"""
    <div class="kpi-card{cls}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)


def section_header(title: str):
    st.markdown(f'<div class="section-hdr">{title}</div>', unsafe_allow_html=True)


def show_aging_summary(summary: dict):
    """แสดง KPI cards + insights สำหรับ Aging Report"""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("Total SOH (มียอด)", f"{summary['total_soh_qty']:,.0f} pcs",
                 f"Retail ฿{summary['total_retail']/1e6:.1f}M")
    with col2:
        kpi_card("Cost Value รวม", f"฿{summary['total_cost']/1e6:.1f}M",
                 "ต้นทุนที่จมอยู่ใน stock", "purple")
    with col3:
        dead_pct = summary['dead_pct']
        color = "red" if dead_pct > 20 else "amber"
        kpi_card("Dead Stock (>360 วัน)",
                 f"{summary['dead_qty']:,.0f} pcs ({dead_pct:.1f}%)",
                 f"ต้นทุนจม ฿{summary['dead_cost']/1e6:.1f}M", color)
    with col4:
        kpi_card("ไม่มี On Floor Date", f"{summary['error_rows']:,} rows",
                 "ต้อง import ข้อมูลก่อน", "amber")

    # Warning banners
    if summary.get("flag_dead_high"):
        st.markdown(f"""<div class="banner-error">
        🔴 <b>Dead Stock สูงกว่า benchmark</b> — ปัจจุบัน {summary['dead_pct']:.1f}%
        (industry benchmark ~15-20%) ควรนำเสนอผู้บริหารเพื่อขอ approve แผนระบาย
        </div>""", unsafe_allow_html=True)
    if summary.get("flag_missing_floor"):
        st.markdown(f"""<div class="banner-warn">
        ⚠️ มี <b>{summary['error_rows']:,} rows</b> ที่ไม่มี On Floor Date
        — ทีมต้อง import ข้อมูลเข้า Items Master ก่อนจึงจะคำนวณ Aging ได้ครบ
        </div>""", unsafe_allow_html=True)

    # Top 10 Dead Stock
    section_header("🚨 Top 10 Dead Stock — ต้นทุนจมสูงสุด")
    top10 = summary.get("top10_dead")
    if top10 is not None and not top10.empty:
        st.dataframe(top10, use_container_width=True, hide_index=True)


def show_sellthrough_summary(summary: dict):
    """แสดง KPI cards สำหรับ Sell-through Report"""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("Net Sales Qty", f"{summary['total_qty']:,.0f} pcs",
                 "จำนวนขายสุทธิ (หัก return)")
    with col2:
        kpi_card("Net Sales Amt", f"฿{summary['total_amt']/1e6:.1f}M",
                 "ยอดขายสุทธิรวม", "purple")
    with col3:
        avg = summary['avg_sell_pct']
        color = "green" if avg >= 60 else ("amber" if avg >= 30 else "red")
        kpi_card("Avg SELL%", f"{avg:.1f}%",
                 "Benchmark: >80%=ดีมาก / <30%=ต้องระวัง", color)
    with col4:
        kpi_card("SKU ที่มียอด", f"{summary['n_sku']:,}",
                 f"{summary['n_location']} locations")

    if summary.get("flag_low_sell"):
        st.markdown("""<div class="banner-warn">
        ⚠️ <b>Avg SELL% ต่ำกว่า 30%</b> — ควรวางแผนโปรโมชันหรือลดราคาสินค้าที่ขายช้า
        </div>""", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    with col_a:
        section_header("🏆 Top 10 Best Sellers")
        top10 = summary.get("top10_sellers")
        if top10 is not None and not top10.empty:
            st.dataframe(top10, use_container_width=True, hide_index=True)
    with col_b:
        section_header("⚠️ Low Sell-through")
        low10 = summary.get("low_sellthrough")
        if low10 is not None and not low10.empty:
            st.dataframe(low10, use_container_width=True, hide_index=True)


def show_accumulated_summary(summary: dict):
    """แสดง KPI cards สำหรับ Accumulated Sales"""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("Total Qty Sold", f"{summary['total_qty']:,.0f} pcs",
                 "ยอดขายรวมทุกเดือน")
    with col2:
        avg_mh = summary['avg_mh']
        color = "red" if avg_mh > 6 else ("amber" if avg_mh > 3 else "green")
        kpi_card("Avg MH", f"{avg_mh:.1f} เดือน",
                 ">6M=slow moving / >3M=ระวัง", color)
    with col3:
        kpi_card("Peak Month", summary.get("peak_month", "-"),
                 f"{summary['monthly_totals'].get(summary.get('peak_month',''), 0):,.0f} pcs", "purple")
    with col4:
        kpi_card("SKU ที่มียอด", f"{summary['n_sku']:,}",
                 f"{summary['n_location']} locations")

    if summary.get("flag_high_mh"):
        st.markdown("""<div class="banner-warn">
        ⚠️ <b>Avg MH สูงกว่า 6 เดือน</b> — สินค้าขายช้า ควรวางแผนโปรโมชันหรือย้ายสาขา
        </div>""", unsafe_allow_html=True)

    # Monthly trend chart
    section_header("📅 Monthly Sales Trend")
    monthly = summary.get("monthly_totals", {})
    if monthly:
        df_monthly = pd.DataFrame(
            [(m, q) for m, q in monthly.items() if q > 0],
            columns=["Month", "Qty"]
        )
        if not df_monthly.empty:
            import plotly.express as px
            fig = px.bar(df_monthly, x="Month", y="Qty",
                         color_discrete_sequence=["#1F4E79"],
                         text="Qty")
            fig.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
            fig.update_layout(height=300, margin=dict(t=20, b=20),
                              plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        section_header("🏆 Top 10 Best Sellers")
        top10 = summary.get("top10_sellers")
        if top10 is not None and not top10.empty:
            st.dataframe(top10, use_container_width=True, hide_index=True)
    with col_b:
        section_header("⚠️ Slow Moving (MH สูง)")
        slow = summary.get("slow_moving")
        if slow is not None and not slow.empty:
            st.dataframe(slow, use_container_width=True, hide_index=True)


# ============================================================
# SECTION 8: Sidebar
# ============================================================

with st.sidebar:
    # --- Logo ---
    col_logo, col_title = st.columns([1, 2])
    with col_logo:
        st.image("logo.png", width=60)
    with col_title:
        st.markdown(
            "<div style='padding-top:8px; font-size:1rem; font-weight:600; color:#E2EAF4;'>YMF Report<br>Portal</div>",
            unsafe_allow_html=True
        )
    st.markdown("---")

    # --- Report selector ---
    st.markdown("#### 📋 เลือกรายงาน")
    report_choice = st.radio(
        label="report",
        options=["Inventory Aging", "Sell-through", "Accumulated Sales"],
        label_visibility="collapsed"
    )

    # Description ต่อรายงาน
    desc_map = {
        "Inventory Aging": (
            "📦 **Inventory Aging**\n\n"
            "ดูอายุสินค้าในคลัง ณ วันที่ระบุ\n"
            "แบ่งเป็น bucket เช่น 0-30 วัน, 31-60 วัน...\n"
            "ใช้วางแผน **ระบายสินค้าค้างสต็อก**"
        ),
        "Sell-through": (
            "🛍️ **Sell-through**\n\n"
            "วัด % สินค้าที่ขายออกเทียบกับที่รับเข้า\n"
            "SELL% = ยอดขาย ÷ (BOM + GR)\n"
            "ใช้ประเมิน **performance รายสาขา**\n"
            "และวางแผน **โปรโมชัน/เติม stock**"
        ),
        "Accumulated Sales": (
            "📅 **Accumulated Sales**\n\n"
            "ยอดขายสะสมแยกตามเดือน Jan-Dec\n"
            "คำนวณ MH (Month of Hand) ว่าสินค้า\n"
            "เหลืออีกกี่เดือนจึงจะหมด\n"
            "ใช้ติดตาม **แนวโน้มและ slow moving**"
        ),
    }
    st.info(desc_map[report_choice])

    st.markdown("---")

    # --- Date inputs ---
    st.markdown("#### 📅 ตั้งค่าวันที่")

    if report_choice == "Inventory Aging":
        st.caption("เลือกวันที่ต้องการดูสต็อก ณ วันนั้น\nตัวอย่าง: วันนี้ = ดูสต็อกปัจจุบัน")
        as_of_date = st.date_input(
            "As of Date",
            value=date.today(),
        )
    else:
        st.caption(
            "เลือกช่วงเวลาที่ต้องการดูยอดขาย\n"
            "ตัวอย่าง: 01/01/2026 – วันนี้\n"
            "= ดูยอดขายตั้งแต่ต้นปีจนถึงวันนี้"
        )
        col_f, col_t = st.columns(2)
        with col_f:
            date_from = st.date_input("From", value=date(date.today().year, 1, 1))
        with col_t:
            date_to   = st.date_input("To",   value=date.today())

    st.markdown("---")

    # --- Threshold Settings ---
    st.markdown("#### ⚙️ Threshold Settings")

    if report_choice == "Inventory Aging":
        st.caption(
            "กำหนดเกณฑ์แบ่งกลุ่มสินค้าตามอายุ\n"
            "🟢 Fresh = สินค้าใหม่ ขายได้ปกติ\n"
            "🟡 Aging = เริ่มนาน ควรเฝ้าระวัง\n"
            "🔴 Dead Stock = ค้างนาน ควรเร่งระบาย"
        )
        fresh_max = st.slider("🟢 Fresh ไม่เกิน (วัน)", 30, 180, 90, step=30)
        aging_max = st.slider("🟡 Aging ไม่เกิน (วัน)", 180, 540, 360, step=30)
        st.caption(f"🔴 Dead Stock = มากกว่า {aging_max} วัน")

    elif report_choice == "Sell-through":
        st.caption(
            "กำหนดเกณฑ์ประเมิน SELL%\n"
            "🟢 High = ขายดีมาก พิจารณาเติม stock\n"
            "🔴 Low = ขายช้า ควรทำโปรโมชัน\n"
            "Benchmark fashion retail: 60-80%"
        )
        sell_high = st.slider("🟢 High SELL% (≥)", 50, 100, 80, step=5,
                              help="SKU ที่ SELL% สูงกว่านี้ = ขายดีมาก")
        sell_low  = st.slider("🔴 Low SELL% (<)",   5,  50, 30, step=5,
                              help="SKU ที่ SELL% ต่ำกว่านี้ = ต้องระวัง")

    elif report_choice == "Accumulated Sales":
        st.caption(
            "กำหนดเกณฑ์ MH (Month of Hand)\n"
            "= จำนวนเดือนที่สต็อกจะหมด\n"
            "🟡 Warning = เริ่มขายช้า ควรติดตาม\n"
            "🔴 Critical = slow moving ชัดเจน\n"
            "Benchmark fashion retail: < 3 เดือน"
        )
        mh_warn     = st.slider("🟡 Warning MH (>เดือน)",  1,  6,  3,
                                help="MH สูงกว่านี้ = เริ่มน่าเป็นห่วง")
        mh_critical = st.slider("🔴 Critical MH (>เดือน)", 3, 24,  6,
                                help="MH สูงกว่านี้ = slow moving ชัดเจน")

    st.markdown("---")

    # --- Run button (ใน sidebar — กดหลัง upload ไฟล์ใน main content แล้ว) ---
    run_btn = st.button(
        "▶ Run Report",
        type="primary",
        use_container_width=True,
    )
    st.caption("⏱ ใช้เวลาประมาณ 1-3 นาที\nขึ้นอยู่กับขนาดของข้อมูล")


# ============================================================
# SECTION 9: Main Content
# ============================================================

# Header
st.markdown(f"# 📊 {report_choice} Report")
st.markdown(f"<small style='color:#6B7280'>YMF International Thai Co., Ltd.</small>",
            unsafe_allow_html=True)
st.divider()

# ============================================================
# File Upload Section — อยู่ใน main content พื้นขาว อ่านง่าย
# ============================================================

REQUIRED_PREFIXES = {
    "items":    ("Items",                              "Items Master"),
    "ns":       ("YMFINVENTORYONHANDREPORT",           "NS On Hand"),
    "erply":    ("Inventory_By_Items",                 "Erply Inventory"),
    "transfer": ("YMFTRANSFERORDERBYITEMLISTResults", "Transfer Order"),
    "sales":    ("YMFSALESDATAWITHCOSTResults",       "Sales Data"),
}

def match_uploads(files):
    matched = {}
    for key, (prefix, label) in REQUIRED_PREFIXES.items():
        for f in files:
            # รองรับทั้ง space และ underscore ในชื่อไฟล์
            clean_name = f.name.replace(" ", "_")
            if clean_name.startswith(prefix) or f.name.startswith(prefix):
                matched[key] = f
                break
    return matched

with st.container():
    st.markdown("### 📂 Step 1 — อัปโหลดไฟล์ข้อมูล")
    st.caption("อัปโหลดไฟล์ดิบจากระบบ NetSuite และ Erply รองรับ .xlsx และ .xls ชื่อไฟล์ไม่ต้องแก้ไข วางได้เลย")

    uploaded_files = st.file_uploader(
        label="เลือกไฟล์ (เลือกได้หลายไฟล์พร้อมกัน)",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        help="อัปโหลดพร้อมกันได้เลย ไม่ต้องทีละไฟล์"
    )

    # แสดงสถานะไฟล์แต่ละตัว
    if uploaded_files:
        matched = match_uploads(uploaded_files)
        cols = st.columns(5)
        for i, (key, (prefix, label)) in enumerate(REQUIRED_PREFIXES.items()):
            with cols[i]:
                if key in matched:
                    st.success(f"✅ {label}")
                else:
                    st.warning(f"⬜ {label}")
    else:
        st.info("⬆️ กรุณาอัปโหลดไฟล์ข้อมูลก่อน จากนั้นกด **▶ Run Report** ใน sidebar")

st.divider()

# Session state สำหรับเก็บผลลัพธ์
if "report_data" not in st.session_state:
    st.session_state.report_data    = None
    st.session_state.report_summary = None
    st.session_state.report_type    = None
    st.session_state.report_params  = {}

# --- แปลง uploaded files เป็น buffers ---
def build_buffers_from_uploads(uploaded_files: list) -> dict:
    """แปลง st.uploaded_files เป็น dict ของ BytesIO buffers"""
    import io as _io
    matched = match_uploads(uploaded_files)
    buffers = {}
    for key, f in matched.items():
        buf = _io.BytesIO(f.read())
        buf.seek(0)
        buffers[key] = buf
    return buffers

# Run
if run_btn:
    buffers = build_buffers_from_uploads(uploaded_files)

    try:
        if report_choice == "Inventory Aging":
            with st.spinner("⏳ กำลังคำนวณ Inventory Aging..."):
                df_report, summary = run_aging_report(buffers, as_of_date)
            st.session_state.report_type   = "aging"
            st.session_state.report_params = {"as_of_date": as_of_date}

        elif report_choice == "Sell-through":
            with st.spinner("⏳ กำลังคำนวณ Sell-through..."):
                df_report, summary = run_sellthrough_report(buffers, date_from, date_to)
            st.session_state.report_type   = "sellthrough"
            st.session_state.report_params = {"date_from": date_from, "date_to": date_to}

        elif report_choice == "Accumulated Sales":
            with st.spinner("⏳ กำลังคำนวณ Accumulated Sales..."):
                df_report, summary = run_accumulated_report(buffers, date_from, date_to)
            st.session_state.report_type   = "accumulated"
            st.session_state.report_params = {"date_from": date_from, "date_to": date_to}

        st.session_state.report_data    = df_report
        st.session_state.report_summary = summary
        st.markdown('<div class="banner-success">✅ ประมวลผลเสร็จแล้ว</div>',
                    unsafe_allow_html=True)

    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาด: {e}")
        st.exception(e)
        st.stop()

# แสดงผล
if st.session_state.report_data is not None:
    df_report = st.session_state.report_data
    summary   = st.session_state.report_summary
    rtype     = st.session_state.report_type
    params    = st.session_state.report_params

    # Summary cards
    if rtype == "aging":
        show_aging_summary(summary)
    elif rtype == "sellthrough":
        show_sellthrough_summary(summary)
    elif rtype == "accumulated":
        show_accumulated_summary(summary)

    st.divider()

    # Filter + Table
    section_header("🔍 ข้อมูลรายละเอียด")

    # Filter bar
    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 2])

    with col_f1:
        # Location filter
        loc_col = "location" if "location" in df_report.columns else None
        if loc_col:
            locs = ["ทั้งหมด"] + sorted(df_report[loc_col].dropna().unique().tolist())
            sel_loc = st.selectbox("Location", locs)
        else:
            sel_loc = "ทั้งหมด"

    with col_f2:
        # Product Status filter
        if "Product Status" in df_report.columns:
            statuses = ["ทั้งหมด"] + sorted(df_report["Product Status"].dropna().unique().tolist())
            sel_status = st.selectbox("Product Status", statuses)
        else:
            sel_status = "ทั้งหมด"

    with col_f3:
        # Class filter
        if "Class" in df_report.columns:
            classes = ["ทั้งหมด"] + sorted(df_report["Class"].dropna().unique().tolist())
            sel_class = st.selectbox("Class (Brand)", classes)
        else:
            sel_class = "ทั้งหมด"

    with col_f4:
        # Search item code
        search = st.text_input("🔍 ค้นหา Item Code", placeholder="เช่น BL-1023943")

    # Apply filters
    df_display = df_report.copy()
    if sel_loc != "ทั้งหมด" and loc_col:
        df_display = df_display[df_display[loc_col] == sel_loc]
    if sel_status != "ทั้งหมด" and "Product Status" in df_display.columns:
        df_display = df_display[df_display["Product Status"] == sel_status]
    if sel_class != "ทั้งหมด" and "Class" in df_display.columns:
        df_display = df_display[df_display["Class"] == sel_class]
    if search:
        item_col = "item_code" if "item_code" in df_display.columns else "Material Code"
        if item_col in df_display.columns:
            df_display = df_display[
                df_display[item_col].str.contains(search, case=False, na=False)
            ]

    st.caption(f"แสดง {len(df_display):,} รายการ จากทั้งหมด {len(df_report):,} รายการ")
    st.dataframe(df_display, use_container_width=True, hide_index=True, height=400)

    # Download button
    st.divider()
    col_dl, col_info = st.columns([2, 5])
    with col_dl:
        with st.spinner("กำลังสร้างไฟล์ Excel..."):
            excel_bytes = export_to_bytes(df_report, rtype, **params)
        fname_map = {
            "aging":       f"Inventory_Aging_{params.get('as_of_date','')}.xlsx",
            "sellthrough": f"Sellthrough_{params.get('date_from','')}_{params.get('date_to','')}.xlsx",
            "accumulated": f"Accumulated_{params.get('date_from','')}_{params.get('date_to','')}.xlsx",
        }
        st.download_button(
            label="⬇️ Download Excel",
            data=excel_bytes,
            file_name=fname_map.get(rtype, "report.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )
    with col_info:
        st.caption(f"Excel จะมี 2 sheets: รายงานหลัก + MER Summary (KPI cards, top items, action items)")

else:
    # Placeholder — ยังไม่ได้รัน
    st.markdown("""
    <div style="text-align:center; padding: 60px 0; color: #9CA3AF;">
        <div style="font-size: 3rem; margin-bottom: 16px;">📊</div>
        <div style="font-size: 1.1rem; font-weight: 500; color: #6B7280;">
            เลือกรายงานและตั้งค่าวันที่ใน Sidebar<br>
            แล้วกด <b>▶ Run Report</b> เพื่อเริ่มประมวลผล
        </div>
    </div>
    """, unsafe_allow_html=True)
