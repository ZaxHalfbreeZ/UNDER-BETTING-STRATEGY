import streamlit as st
import requests
import pandas as pd
import time
import io
import sqlite3
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

st.set_page_config(page_title="Under Bot v3.1", page_icon="🚀", layout="wide")

# ==========================================
# ตั้งค่าฐานข้อมูล SQLite
# ==========================================
def init_db():
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS pending_bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  scan_date TEXT, game_id TEXT, home TEXT, away TEXT,
                  league TEXT, xg REAL, poisson REAL, score REAL, odds REAL, stake REAL,
                  status TEXT DEFAULT 'pending', profit REAL DEFAULT 0)''')
    conn.commit()
    conn.close()

def save_bets_to_db(bets, scan_date):
    if not bets: return
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    for b in bets:
        c.execute('''INSERT INTO pending_bets 
                     (scan_date, game_id, home, away, league, xg, poisson, score, odds, stake) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (scan_date, b['game_id'], b['ทีมเหย้า'], b['ทีมเยือน'], b['🏆 ลีก'],
                   b['xG รวม'], b['Poisson U2.5 (%)'], b['คะแนน'], b['✏️ Odds U2.5'], b['stake_amount']))
    conn.commit()
    conn.close()

def get_pending_bets(date_str):
    conn = sqlite3.connect('betting_log.db')
    df = pd.read_sql_query("SELECT * FROM pending_bets WHERE scan_date=? AND status='pending'", conn, params=(date_str,))
    conn.close()
    return df

def update_bet_result(bet_id, status, profit):
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    c.execute("UPDATE pending_bets SET status=?, profit=? WHERE id=?", (status, profit, bet_id))
    conn.commit()
    conn.close()

init_db()

# ==========================================
# ตั้งค่า API
# ==========================================
API_KEY = st.secrets["API_KEY"]
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

# ==========================================
# ฟังก์ชันคำนวณ
# ==========================================
def calculate_poisson_under(lambda_home, lambda_away):
    under_prob = 0
    for i in range(3):
        for j in range(3 - i):
            under_prob += poisson.pmf(i, lambda_home) * poisson.pmf(j, lambda_away)
    return under_prob * 100

def calculate_score(combined_xg, poisson_under, xg_target):
    norm_xg = max(0, 1 - (combined_xg / xg_target)) if combined_xg < xg_target else 0
    norm_poisson = min(1.0, poisson_under / 52) if poisson_under > 52 else 0
    score = (norm_xg * 40) + (norm_poisson * 60)
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
                        if val and val >= 1.50: return val
        return 0.0 
    except: return 0.0

def calculate_kelly_stake(odds, probability, bankroll):
    if not odds or odds <= 1.0 or not bankroll: return 0.0, 0.0
    b = odds - 1; p = probability / 100.0; q = 1 - p
    kelly = (b * p - q) / b; fractional_kelly = kelly * 0.30
    stake_pct = max(0, min(fractional_kelly * 100, 5.0))
    bet_amount = bankroll * (stake_pct / 100)
    return round(stake_pct, 1), round(bet_amount)

def format_match_time(date_str):
    if not date_str: return "N/A"
    try:
        dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        thai_tz = timezone(timedelta(hours=4)) 
        return dt.astimezone(thai_tz).strftime("%H:%M")
    except: return "N/A"

def create_excel_with_formula(df_edited, current_bankroll):
    output = io.BytesIO()
    try: import xlsxwriter
    except ImportError: return None
    excel_data = []
    for index, row in df_edited.iterrows():
        if row['✏️ Odds U2.5'] >= 1.50:
            excel_data.append({'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}", 'ใส่ Odds ตรงนี้': row['✏️ Odds U2.5'], 'Poisson U2.5 (%)': row['Poisson U2.5 (%)']})
    if not excel_data: return None
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_excel = pd.DataFrame(excel_data)
        df_excel.to_excel(writer, index=False, sheet_name='Bet Plan', startrow=1, header=False)
        workbook = writer.book; worksheet = writer.sheets['Bet Plan']
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#4F81BD', 'font_color': 'white', 'border': 1})
        headers = ['คู่บอล', 'ใส่ Odds ตรงนี้', 'Poisson U2.5 (%)', '💰 เงินแทงอัตโนมัติ (บาท)']
        for col_num, header in enumerate(headers): worksheet.write(0, col_num, header, header_format)
        worksheet.set_column('A:A', 35); worksheet.set_column('B:B', 25); worksheet.set_column('C:C', 20); worksheet.set_column('D:D', 30)
        for row_num in range(1, len(excel_data) + 1):
            formula = f'=IF(B{row_num}>=1.5, MIN(MAX((((B{row_num}-1)*(C{row_num}/100)-(1-(C{row_num}/100)))/(B{row_num}-1))*30, 0), 5) * {current_bankroll} / 100, 0)'
            worksheet.write_formula(row_num, 3, formula)
        money_format = workbook.add_format({'num_format': '#,##0" ฿"', 'align': 'center'})
        for row_num in range(1, len(excel_data) + 1): worksheet.set_format(row_num, 3, money_format)
    output.seek(0)
    return output

# ==========================================
# ✅ ส่วนสำคัญ: เตรียมหน่วยความจำ (Session State) ให้พร้อม
# ==========================================
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = []
if 'near_misses' not in st.session_state:
    st.session_state.near_misses = []

# ==========================================
# UI หลัก
# ==========================================
st.title("🚀 UNDER BOT - Smart Logging Edition")
tab1, tab2 = st.tabs(["🔍 สแกน & บันทึกคู่วันนี้", "📊 ตรวจสอบผลรางวัลเมื่อวาน"])

# ==========================================
# TAB 1
# ==========================================
with tab1:
    with st.expander("⚙️ ตั้งค่าเกณฑ์การคัดกรอง", expanded=False):
        col1, col2 = st.columns(2)
        with col1: score_target = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ", min_value=40, max_value=80, value=60, step=5)
        with col2: xg_target = st.slider("📊 ค่า xG รวมสูงสุดที่ยอมรับ", min_value=2.0, max_value=3.5, value=2.6, step=0.1)
        bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100)

    # ❌ ขั้นตอนที่ 1: ถ้ากดปุ่ม ให้ "ทำงานหนัก" แล้วเก็บผลลัพธ์เข้า Session State
    if st.button("🔍 เริ่มค้นหาคู่เกมวันนี้", type="primary", use_container_width=True):
        today_str = datetime.now().strftime('%Y-%m-%d')
        LIST_URL = f"https://api.sstats.net/games/list?date={today_str}"
        STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}" 
        
        with st.spinner('กำลังดึงรายการแมตช์...'):
            try:
                res_list = requests.get(LIST_URL, headers=HEADERS, timeout=30).json()
                games = [g for g in res_list.get('data', []) if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'postponed']]
            except: games = []

        if not games:
            st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้")
            st.session_state.scan_results = []
        else:
            st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์...")
            temp_approved = []; temp_near = []
            progress_text = st.empty(); progress_bar = st.progress(0)
            
            for index, g in enumerate(games):
                game_id = str(g.get('id')); league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
                country = g.get('season', {}).get('league', {}).get('country', {}).get('name', '')
                home = g.get('homeTeam', {}).get('name', 'Home'); away = g.get('awayTeam', {}).get('name', 'Away')
                raw_date = g.get('date'); progress_text.text(f"กำลังตรวจสอบ: {home} vs {away}"); progress_bar.progress((index + 1) / len(games))
                try:
                    stats_url = STATS_URL_FORMAT.format(game_id)
                    res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=10)
                    if res_stats_req.status_code != 200: time.sleep(0.3); continue 
                    res_stats = res_stats_req.json(); glicko_data = res_stats.get('data', {}).get('glicko', {})
                    home_xg = glicko_data.get('homeXg'); away_xg = glicko_data.get('awayXg')
                    if home_xg is None or away_xg is None: time.sleep(0.3); continue
                    home_xg = float(home_xg); away_xg = float(away_xg); combined_xg = home_xg + away_xg
                    poisson_prob = calculate_poisson_under(home_xg, away_xg)
                    score = calculate_score(combined_xg, poisson_prob, xg_target)
                    u25_odds = get_under_25_odds(g)
                    league_display = f"{country} - {league}" if country else league
                    
                    match_data = {'⏰ เวลา': format_match_time(raw_date), '🏆 ลีก': league_display, 'ทีมเหย้า': home, 'ทีมเยือน': away, 'xG รวม': combined_xg, 'Poisson U2.5 (%)': poisson_prob, 'คะแนน': score, '✏️ Odds U2.5': u25_odds, 'game_id': game_id, 'stake_amount': 0}
                    
                    if score >= score_target: temp_approved.append(match_data)
                    elif score >= (score_target - 10): temp_near.append(match_data)
                    time.sleep(0.5)
                except: time.sleep(1); continue
            
            progress_bar.empty(); progress_text.empty()
            # ✅ ส่วนสำคัญ: เก็บข้อมูลเข้า Memory แทนที่จะแสดงตรงนี้
            st.session_state.scan_results = temp_approved
            st.session_state.near_misses = temp_near

    # ✅ ขั้นตอนที่ 2: แสดงผลตาราง "ข้างนอก" ปุ่มกด (จะไม่หายแม้คุณจะพิมพ์แก้ไข)
    if st.session_state.scan_results:
        st.divider(); st.success(f"🎯 พบ **{len(st.session_state.scan_results)} คู่** ที่ผ่านเกณฑ์! (สามารถแก้ไขตัวเลขได้เลย)")
        df = pd.DataFrame(st.session_state.scan_results)
        df = df.sort_values(by='คะแนน', ascending=False).reset_index(drop=True)
        
        # ตารางนี้จะอ่านค่าจาก Session State มาแสดง พอคุณแก้ค่า มันจะเก็บค่าใหม่ไว้ใน edited_df
        edited_df = st.data_editor(df, disabled=["⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน", "xG รวม", "Poisson U2.5 (%)", "คะแนน"], width="stretch", height=400, hide_index=True)
        
        # คำนวณเงินแทงจากค่าที่ถูกแก้ไขแล้ว
        final_bets_to_save = []
        for index, row in edited_df.iterrows():
            odds = row['✏️ Odds U2.5']; prob = row['Poisson U2.5 (%)']
            if odds >= 1.50: 
                stake_pct, bet_amount = calculate_kelly_stake(odds, prob, bankroll)
                if bet_amount > 0: 
                    row['stake_amount'] = bet_amount
                    final_bets_to_save.append(row)
                    
        if final_bets_to_save:
            df_bets = pd.DataFrame(final_bets_to_save)[['ทีมเหย้า', 'ทีมเยือน', '✏️ Odds U2.5', 'stake_amount']].rename(columns={'stake_amount': '💰 แทง (บาท)'})
            df_bets['💰 แทง (บาท)'] = df_bets['💰 แทง (บาท)'].apply(lambda x: f"{x:.0f} ฿")
            st.dataframe(df_bets, width="stretch", hide_index=True)
            
            excel_file = create_excel_with_formula(edited_df, bankroll)
            if excel_file:
                st.download_button(label="📥 ดาวน์โหลดไฟล์ Excel", data=excel_file, file_name=f'Under_Bet_{datetime.now().strftime("%Y-%m-%d")}.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

            if st.button("💾 ยืนยันการบันทึกคู่เหล่านี้เพื่อตรวจสอบผลวันพรุ่ง", type="secondary", use_container_width=True):
                today_str = datetime.now().strftime('%Y-%m-%d')
                save_bets_to_db(final_bets_to_save, today_str)
                st.success("✅ บันทึกลงระบบสำเร็จแล้ว! พรุ่งนี้มากด Tab 2 เพื่อดูผลลัพธ์ได้เลย")
                st.session_state.scan_results = [] # ล้างตารางหลังบันทึก
        else: 
            st.warning("กรุณาใส่เลข Odds ที่มากกว่า 1.50 เพื่อคำนวณเงินแทง")

    if st.session_state.near_misses:
        st.divider(); st.warning(f"⚠️ คู่ที่ใกล้เคียงเกณฑ์")
        df_near = pd.DataFrame(st.session_state.near_misses)
        df_near = df_near.sort_values(by='คะแนน', ascending=False).head(3).reset_index(drop=True)
        st.dataframe(df_near, width="stretch", hide_index=True)


# ==========================================
# TAB 2
# ==========================================
with tab2:
    st.markdown("### 📈 ระบบตรวจสอบผลแทง (จากคู่ที่คุณบันทึกไว้จริงๆ)")
    yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    pending_df = get_pending_bets(yesterday_date)
    
    if pending_df.empty:
        st.info(f"ไม่มีคู่บอลที่รอตรวจสอบผลสำหรับวันที่ {yesterday_date}")
    else:
        st.warning(f"พบ {len(pending_df)} คู่ที่บันทึกไว้เมื่อวาน กำลังดึงสกอร์จริงมาเปรียบเทียบ...")
        
        if st.button("🔄 ดึงผลการแข่งขันจากเมื่อวาน", type="primary", use_container_width=True):
            results = []
            progress_text = st.empty(); progress_bar = st.progress(0)
            LIST_URL_YEST = f"https://api.sstats.net/games/list?date={yesterday_date}"
            
            try:
                res_list = requests.get(LIST_URL_YEST, headers=HEADERS, timeout=15).json()
                all_yesterday_games = {str(g.get('id')): g for g in res_list.get('data', []) if g.get('statusName', '').lower() == 'finished'}
            except:
                all_yesterday_games = {}

            for index, row in pending_df.iterrows():
                game_id = row['game_id']
                progress_text.text(f"ตรวจสอบ: {row['home']} vs {row['away']}")
                progress_bar.progress((index + 1) / len(pending_df))
                
                if game_id in all_yesterday_games:
                    g = all_yesterday_games[game_id]
                    home_ft = g.get('homeFTResult', 0) or 0; away_ft = g.get('awayFTResult', 0) or 0
                    total_goals = int(home_ft) + int(away_ft)
                    is_win = total_goals <= 2
                    profit_loss = (row['stake'] * (row['odds'] - 1)) if is_win else -row['stake']
                    
                    status_str = '✅ ได้' if is_win else '❌ เสีย'
                    update_bet_result(row['id'], 'won' if is_win else 'lost', profit_loss)
                    
                    results.append({
                        '🏆 ลีก': row['league'],
                        'คู่บอล': f"{row['home']} vs {row['away']}",
                        'สกอร์จริง': f"{home_ft}-{away_ft} (รวม {total_goals})",
                        '💰 เดิมพัน': f"{row['stake']:.0f} ฿",
                        'ผลลัพธ์': status_str,
                        'กำไร/ขาดทุน': f"{'+' if profit_loss > 0 else ''}{profit_loss:.0f} ฿"
                    })
                else:
                    results.append({
                        '🏆 ลีก': row['league'],
                        'คู่บอล': f"{row['home']} vs {row['away']}",
                        'สกอร์จริง': 'ไม่พบข้อมูล (อาจเลื่อน)',
                        '💰 เดิมพัน': f"{row['stake']:.0f} ฿",
                        'ผลลัพธ์': '⏸️ ไม่แข่ง',
                        'กำไร/ขาดทุน': '0 ฿'
                    })
                time.sleep(0.5)
                
            progress_bar.empty(); progress_text.empty()
            
            if results:
                st.divider()
                df_results = pd.DataFrame(results)
                
                valid_results = [r for r in results if 'ไม่แข่ง' not in r['ผลลัพธ์']]
                if valid_results:
                    total_pl = sum([float(str(p).replace(' ฿','').replace('+','')) for p in [r['กำไร/ขาดทุน'] for r in valid_results]])
                    wins = len([r for r in valid_results if 'ได้' in r['ผลลัพธ์']])
                    win_rate = (wins / len(valid_results)) * 100
                    total_staked = sum([float(str(s).replace(' ฿','')) for s in [r['💰 เดิมพัน'] for r in valid_results]])
                    roi = (total_pl / total_staked) * 100 if total_staked > 0 else 0
                    
                    c1, c2, c3 = st.columns(3)
                    c1.metric("คู่ที่แทง", len(valid_results))
                    c2.metric("Win Rate", f"{win_rate:.1f}%")
                    c3.metric("ROI จริง", f"{roi:.2f}%", delta=f"{total_pl:.0f} ฿")
                
                st.dataframe(df_results, width="stretch", hide_index=True)
                st.success("✅ อัปเดตผลแทงลงในระบบแล้ว คู่เหล่านี้จะไม่ถูกนำมาคำนวณซ้ำ")
