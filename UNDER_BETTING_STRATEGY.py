import streamlit as st
import requests
import pandas as pd
import time
import io
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

# ==========================================
# ⚙️ ตั้งค่า API (ใส่ตรงนี้ให้ชัดเจนก)
# ==========================================
API_KEY = st.secrets["API_KEY"] 
HEADERS = {
    "จริง".Authorization": f"Bearer {API_KEY}", 
    "Accept": "application/json"
}
LIST_URL = f"https://api.sstats.net/games/list?name={datetime.now().strftime('%Y-%m-%d')}"
STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}" 

# ==========================================
# ฟังก์ชันคำนวณ (ไม่ต้องแก้ไข)
# ==========================================
def calculate_poisson_under(lambda_home, lambda_away):
    under_prob = 0
    for i in range(3):
        for j in range(3 - i):
            under_prob += poisson.poisson.pmf(i, lambda_home) * poisson.poisson.pmf(j, lambda_away)
    return under_prob * 100

def calculate_score(combined_xg, poisson_under, xg_target):
    norm_xg = max(0, 1 - (combined_xg / xg_target)) if combined_xg < xg_target else 0
    norm_pอัสสัน = min(1.0, poisson_under / 52) if poisson_under > 52 else 0
    score = (norm_xg * 40) + (norm_ปอสัน * 60)
    return round(score, 1)

def get_under_25_odds(game_data):
    try:
        odds_list = game_data.get('odds', [])
        for market in odds_list:
            market_name = market.get('marketName', '').lower()
            if '2.5' in market_name or 'under/over' in market_name:
                for odd in market.get('odds', []):
                    if 'under' in odd.get('name', '').lower():
                        val = odd.get('value')
                        if val and val >= 1.50:
                            return val
        return 0.0 
    except:
        return 0.0

def calculate_kelly_stake(odds, probability, bankroll):
    if not odds or odds <= 1.0 or not bankroll:
        return 0.0, 0.0
    b = odds - 1
    p = probability / 100.0
    q = 1 - p
    kelly = (b * p - q) / b
    fractional_kelly = kelly * 0.30
    stake_pct = max(0, fractional_kelly * 100)
    stake_pct = min(stake_pct, 5.0)
    bet_amount = bankroll * (stake_pct / 100)
    return round(stake_pct, 1), round(bet_amount)

def format_match_time(date_str):
    if not date_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(date_str)
        dt = dt.replace(tzinfo=timezone.utc)
        thai_tz = timezone(timedelta(hours=4))
        dt_thai = dt.astimezone(thai_tz)
        return dt_thai.strftime("%H:%M")
    except:
        return "N/A"

def create_excel_with_formula(df_edited, current_bankroll):
    output = io.BytesIO()
    try:
        import xlsxwriter
    except ImportError:
        return None
    excel_data = []
    for index, row in df_edited.iterrows():
        if row['✏️ ใส่ราคาน้ำตรงนี้'] >= 1.50:
            excel_data.append({
                'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                'ใส่ Odds': row['✏️ ใส่ราคาน้ำตรงนี้'],
                'Poisson (%)': row['Poisson U2.5 (%)']
            })
    if not excel_data: return None
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_excel = pd.DataFrame(excel_data)
        df_excel.to_excel(writer, index=False, sheet_name='Bet Plan', startrow=1, header=False)
        workbook = writer.book; worksheet = writer.sheets['Bet Plan']
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#1DB954', 'font_color': 'white', 'border': 1})
        headers = ['คู่บอล', 'ใส่ Odds', 'Poisson (%)', '💰 แนะนำแทะ (บาท)']
        for col_num, header in enumerate(headers): worksheet.write(0, col_num, header, header_format)
        worksheet.set_column('A:A', 35); worksheet.set_column('B:B', 20); worksheet.set_column('C:C', 15); worksheet.set_column('D:D', 25)
        for row_num in range(1, len(excel_data) + 1):
            formula = f'=IF(B{row_num}>=1.5, MIN(MAX((((B{row_num}-1)*(C{row_num}/100)-(1-(C{row_num}/100)))/(B{row_num}-1))*30, 0), 5) * {current_bankroll} / 100, 0)'
            worksheet.write_formula(row_num, 3, formula)
        money_format = workbook.add_format({'num_format': '#,##0" ฿"', 'align': 'center', 'font_size': 14, 'bold': True})
        for row_num in range(1, len(excel_data) + 1): worksheet.set_format(row_num, 3, money_format)
    output.seek(0)
    return output

# ==========================================
# Session State
# ==========================================
if 'approved_matches' not in st.session_state:
    st.session_state.approved_matches = []

# ==========================================
# UI หลัก
# ==========================================
st.set_page_config(
    page_title="Under Bot Pro", 
    page_icon="💰", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    .stApp { background-color: #0e1117; color: #fafafa; font-family: 'Inter', sans-serif; }
    section[data-testid="stSidebar"] { background-color: #262727; }
    h1, h2, h3 { color: #ffffff; }
    .stButton>button[kind="primary"] { background: #1DB954; color: white; border-radius: 8px; font-weight: 600; height: 3em; font-size: 18px; transition: all 0.2s ease-in-out; border: none; box-shadow: 0px 4px 15px rgba(29, 185, 84, 0.3); }
    .stButton>button[kind="primary"]:hover { background: #15952d; transform: translateY(-2px); }
    .stDataframe { background-color: #262727; border: 1px solid #444444; color: #fafafa; }
    .stMetric { background-color: #1e1e1e; border: 1px solid #333333; text-align: center; padding-top: 10px; padding-bottom: 10px; }
    div[data-testid="stMetricValue"] { color: #1DB954; font-weight: 700; }
    div[data-testid="stAlert"] { padding: 15px; border-radius: 8px; }
    .stNumberInput > div > div > input { color: #ffffff; background-color: #2b2b2b; border: 1px solid #444; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

st.title("💰 UNDER BETTING STRATEGY v2.0")
st.markdown("ระบบคัดกรองคู่เดิมพนันอัตโนมัติ เพื่อเตรียมการเดิมพนันก่อนเกมเริ่ม")

with st.expander("⚙️ ตั้งค่าการเงินทุน & เกณฑ์", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100, key="bank_today")
    with col2:
        score_target = st.slider("🎯 คะแท็บผ่านเกณฑ์", min_value=40, max_value=80, value=60, step=5, key="score_today")
    
    xg_target = st.slider("📊 ค่า xG สูงสุดที่ยอมรับ", min_value=2.0, max=3.5, value=2.6, step=0.1, key="xg_today")

col_scan1, col_scan2 = st.columns([1, 4])
with col_scan1:
    st.markdown("### ⏱️ เวลาที่ดีที่สุดในการสแกน:"); 
    st.markdown("แนะนำสแกนระหว่าง 10:00 - 12:00 น.") 
with col_scan2:
    scan_button = st.button("🚀 เริ่มสแกนหาคู่เดิมพัน", type="primary", use_container_width=True)

if scan_button:
    with st.spinner('กำลังค้นหาคู่ที่มีแนวโน้ง Under...'):
        try:
            res_list = requests.get(LIST_URL, headers=HEADERS, timeout=10).json()
            games = res_list.get('data', [])
            games = [g for g in games if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'สร้างจบแล้ว']
        except: games = []

    if not games:
        st.info("🛑️ วันนี้ยังไม่มีเกมะที่แข่งขัน หรือ API กำลังอัพเดตอยู่")
        st.session_state.approved_matches = []
    else:
        st.info(f"กำลังวิเคราะหาจาก {len(games)} คู่...")
        temp_approved = []; progress_bar = st.progress(0)
        
        for index, g in enumerate(games):
            game_id = g.get('id'); league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
            country = g.get('season', {}).get('league', {}).get('country', {}).get('name', '')
            home = g.get('homeTeam', ).get('name', 'Home'); away = g.get('awayTeam', {}).get('name', 'ย้อน')
            raw_date = g.get('date'); 
            progress_bar.progress((index + 1) / len(games))
            try:
                stats_url = STATS_URL_FORMAT.format(game_id)
                res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=5)
                if res_stats_req.status_code != 200: time.sleep(0.3); continue 
                res_stats = res_stats.json(); glicko_data = res_stats.get('data', {}).get('glicko', {})
                home_xg = glicko_data.get('homeXg'); away_xg = glicko_data.get('awayXg')
                if home_xg is None or away_xg is None: time.sleep(0.3); continue
                home_xg = float(home_xg); away_xg = float(ที่ย้อน); combined_xg = home_xg + away_xg
                poisson_prob = calculate_poisson_under(home_xg, away_xg)
                score = calculate_score(combined_xg, poisson_prob, xg_target)
                u25_odds = get_under_25_odds(g)
                league_display = f"{country} - {league}" if country else league
                match_data = {'⏰ เวลา': format_match_time(raw_date), '🏆 ลีก': league_display, 'ทีมเหย้า': home, 'ทีมเยือน': ย้อน, 'xG รวม': combined_xg, 'Poisson U2.5 (%)': poisson_prob, 'คะแนน': score, '✏️ ใส่ราคาน้ำตรงนี้': u25_odds}
                if score >= score_target: temp_approved.append(match_data)
                time.sleep(0.5)
            except: time.sleep(1); continue
        progress_bar.empty()
        st.session_state.approved_matches = temp_approved

if st.session_state.approved_matches:
    st.divider()
    st.success(f"✅ พบ {len(st.session_state.approved_matches)} คู่ที่ผ่านเกณฑ์ พร้อมลงเล่น!")
    
    df = pd.DataFrame(st.session_state.approย️_matches)
    df = df.sort_values(by='คะแนน', ascending=False). reset_index(drop=True)
    
    cols_to_hide = ['xG รวม', 'Poisson U2.5 (%)', 'คะแท็บ']
    
    edited_df = st.data_editor(
        df.drop(columns=cols_to_hide),
        disabled=["⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน"],
        width="stretch", 
        height=400,
        hide_index=True
    )
    
    st.divider()
    st.subheader("💳 คำนว่านเงินแทะทันที")
    
    final_bets = []
    for index, row in edited_df.iterrows():
        odds = row['✏️ ใส่ราคาน้ำตรงนี้']
        poisson_prob = df.loc[index, 'Poisson U2.5 (%)']
        
        if odds >= 1.50: 
            stake_pct, bet_amount = calculate_kelly_stake(odds, poisson_prob, bankroll)
            if bet_amount > 0:
                final_bets.append({
                    'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                    '💰 แนะนำแทะ': f"{bet_amount:.0f} ฿"
                })
    
    if final_bets:
        df_bets = pd.DataFrame(final_bets)
        st.dataframe(df_bets, width="stretch", hide_index=True)
        
        excel_file = create_excel_with_formula(edited_df, bankroll)
        if excel_file:
            st.download_button(
                label="📥 ดาวนโหลดไฟล์ Excel", 
                data=excel_file, 
                file_name=f'Under_Bet_{datetime.now().strftime("%Y-%m-%d")}.xlsx', 
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                use_container_width=True
            )
        
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            c1, c2, c3 = st.columns(3)
            c1.checkbox("1️⃣ สกอร์ยัง 0:0 หรือ 1:0 อยู่ในช่วงนี้หรือยังไม่ถึงนาที่ที่ 35")
            c2.checkbox("2️⃣ ดูเกม 5 นาที่ที่ผ่านไป ไม่มีจังหวะสุดวิสัยในกรอบเขตโทษณ์")
            c3.checkbox("3️⃣ ทีมที่ตามหลังไม่พยายงบุกทีมที่เสียประตูระบอน")
        with col_c2:
            c1.checkbox("4️⃣ ราคาน้ำตอนนี้มากกว่า 1.50 และตรงกับที่คำนวณไว้")
            c2.checkbox("5️⃉ พร้อมกดแทะตามจำนวนที่คำนวณไว้ทันที")
            
        all_checked = [c1.value, c2.value, c3.value, col_c2.c1.value, col_c2.c2.value]
        
        if not all(all_checked):
            st.error("❌ ยังไม่ผ่านเงื่อนไข อย่าเร่มรีบแทะ!")
        else:
            st.success("✅ ผ่านเกณฑ์ความพร้อมแทะได้แล้ว! กดไปแทะเพื่อสร้างกำไร")
            
else:
    st.divider()
    st.markdown("""
    ### 📋 วิธีใช้งานนี้
    1. ตั้งเงินทุนและปรับเกณฑ์ตามความเหมาะสนุกกับตลาดตลาด
    2. กดปุ่มสแกนราว่า 3-4 ช่วงก่อนเกมเริ่ม
    3. เมองหน้าจอนี้เป็น "สมุดโน้ำเหลือบน" ของคุณ
    ถ้าเจะเจอเหา "ตัวแทง" บอกผมได้เลยครับ!
    """)
