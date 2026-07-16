import streamlit as st
import requests
import pandas as pd
import time
import io
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

# ==========================================
# ตั้งค่าหน้าเว็บ (ต้องอยู่หลัง import ทันที)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")

# ป้องกัน Segmentation fault บนเซิร์ฟเวอร์ฟรี
try:
    import gspread
    import json
    GSPREAD_AVAILABLE = True
except Exception:
    GSPREAD_AVAILABLE = False

# ==========================================
# ตั้งค่า API
# ==========================================
API_KEY = st.secrets["API_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}", 
    "Accept": "application/json"
}

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
        thai_tz = timezone(timedelta(hours=7))
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
        if row['✏️ Odds U2.5'] >= 1.50:
            excel_data.append({
                'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                'ใส่ Odds ตรงนี้': row['✏️ Odds U2.5'],
                'Poisson U2.5 (%)': row['Poisson U2.5 (%)']
            })
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
# ฟังก์ชันบันทึกลง Google Sheets
# ==========================================
def save_to_google_sheet(results_data):
    try:
        service_account_info = json.loads(st.secrets["GCP_SERVICE_ACCOUNT"])
        credentials = gspread.service_account_from_dict(service_account_info)
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key("1jLkfy3S2c59GzliqEGltd9PdaV-ozKGkGiB488ueIPw")
        worksheet = spreadsheet.sheet1
        
        rows_to_add = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        for r in results_data:
            profit_str = str(r.get('กำไร/ขาดทุน', '0')).replace(' ฿', '').replace('+', '')
            rows_to_add.append([
                today_str,
                r.get('ลีก', ''),
                r.get('คู่บอล', ''),
                r.get('สกอร์จริง', ''),
                str(r.get('เดิมพัน', '0')).replace(' ฿', ''),
                'ได้' if 'ได้' in str(r.get('ผลลัพธ์', '')) else 'เสีย',
                profit_str
            ])
            
        if rows_to_add:
            worksheet.append_rows(rows_to_add)
            return True
        return False
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการบันทึก: {e}")
        return False

# ==========================================
# Session State
# ==========================================
if 'approved_matches' not in st.session_state:
    st.session_state.approved_matches = []
if 'near_misses' not in st.session_state:
    st.session_state.near_misses = []

# ==========================================
# UI หลัก
# ==========================================
st.title("🚀 UNDER BOT - Pro Backtesting Edition")

tab1, tab2 = st.tabs(["🔍 สแกนคู่วันนี้", "📊 สรุปผลเมื่อวาน (Backtest)"])

# ==========================================
# TAB 1
# ==========================================
with tab1:
    with st.expander("⚙️ ตั้งค่าเกณฑ์การคัดกรอง", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            score_target = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ", min_value=40, max_value=80, value=60, step=5, key="score_today")
        with col2:
            xg_target = st.slider("📊 ค่า xG รวมสูงสุดที่ยอมรับ", min_value=2.0, max_value=3.5, value=2.6, step=0.1, key="xg_today")
        bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100, key="bank_today")

    if st.button("🔍 เริ่มค้นหาคู่เกมวันนี้", type="primary", use_container_width=True):
        LIST_URL = f"https://api.sstats.net/games/list?date={datetime.now().strftime('%Y-%m-%d')}"
        STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}" 
        
        debug_box = st.expander("🔧 Debug Mode: ตรวจสอบการตอบกลับของ API", expanded=True)
        with debug_box:
            st.write("**URL ที่ส่งไปหา API:**", LIST_URL)
        
        with st.spinner('กำลังดึงรายการแมตช์... (กำลังพยายามเชื่อมต่อหลายรอบ)...'):
            try:
                max_retries = 3
                res = None
                for attempt in range(max_retries):
                    try:
                        res = requests.get(LIST_URL, headers=HEADERS, timeout=30)
                        if res.status_code == 200:
                            break
                    except requests.exceptions.Timeout:
                        if attempt < max_retries - 1:
                            time.sleep(3)
                        else:
                            raise Exception("หมดเวลาเชื่อมต่อหลังจากพยายาม 3 ครั้ง")
                
                with debug_box:
                    st.write("**HTTP Status Code:**", res.status_code)
                    st.write("**ข้อมูลดิบที่ได้รับ:**")
                    try:
                        st.json(res.json())
                    except:
                        st.text(res.text)
                
                if res.status_code == 200:
                    res_list = res.json()
                    games = res_list.get('data', [])
                    games = [g for g in games if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'postponed']]
                else:
                    st.error(f"❌ เกิดข้อผิดพลาด: API ตอบกลับมาด้วย Status {res.status_code}")
                    games = []
                    
            except Exception as e:
                with debug_box:
                    st.error(f"❌ ไม่สามารถเชื่อมต่อกับ API ได้: {e}")
                games = []

        if not games:
            st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้ (หรืออาจจะเกิดจาก Error ข้างต้น)")
            st.session_state.approved_matches = []
        else:
            st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์...")
            temp_approved = []; temp_near = []
            progress_text = st.empty(); progress_bar = st.progress(0)
            
            for index, g in enumerate(games):
                game_id = g.get('id'); league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
                country = g.get('season', {}).get('league', {}).get('country', {}).get('name', '')
                home = g.get('homeTeam', {}).get('name', 'Home'); away = g.get('awayTeam', {}).get('name', 'Away')
                raw_date = g.get('date'); progress_text.text(f"กำลังตรวจสอบ: {home} vs {away}"); progress_bar.progress((index + 1) / len(games))
                try:
                    stats_url = STATS_URL_FORMAT.format(game_id)
                    res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=5)
                    if res_stats_req.status_code != 200: time.sleep(0.3); continue 
                    res_stats = res_stats_req.json(); glicko_data = res_stats.get('data', {}).get('glicko', {})
                    home_xg = glicko_data.get('homeXg'); away_xg = glicko_data.get('awayXg')
                    if home_xg is None or away_xg is None: time.sleep(0.3); continue
                    home_xg = float(home_xg); away_xg = float(away_xg); combined_xg = home_xg + away_xg
                    poisson_prob = calculate_poisson_under(home_xg, away_xg)
                    score = calculate_score(combined_xg, poisson_prob, xg_target)
                    u25_odds = get_under_25_odds(g)
                    league_display = f"{country} - {league}" if country else league
                    match_data = {'⏰ เวลา': format_match_time(raw_date), '🏆 ลีก': league_display, 'ทีมเหย้า': home, 'ทีมเยือน': away, 'xG รวม': combined_xg, 'Poisson U2.5 (%)': poisson_prob, 'คะแนน': score, '✏️ Odds U2.5': u25_odds}
                    if score >= score_target: temp_approved.append(match_data)
                    elif score >= (score_target - 10): temp_near.append(match_data)
                    time.sleep(0.5)
                except: time.sleep(1); continue
            progress_bar.empty(); progress_text.empty()
            st.session_state.approved_matches = temp_approved; st.session_state.near_misses = temp_near

    if st.session_state.approved_matches:
        st.divider(); st.success(f"🎯 พบ **{len(st.session_state.approved_matches)} คู่** ที่ผ่านเกณฑ์!")
        df = pd.DataFrame(st.session_state.approved_matches); df = df.sort_values(by='คะแนน', ascending=False).reset_index(drop=True)
        edited_df = st.data_editor(df, disabled=["⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน", "xG รวม", "Poisson U2.5 (%)", "คะแนน"], width="stretch", height=400, hide_index=True)
        
        st.subheader("💰 สรุปเงินแทงสุดท้าย")
        final_bets = []
        for index, row in edited_df.iterrows():
            odds = row['✏️ Odds U2.5']; prob = row['Poisson U2.5 (%)']
            if odds >= 1.50: 
                stake_pct, bet_amount = calculate_kelly_stake(odds, prob, bankroll)
                if bet_amount > 0: final_bets.append({'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}", '✏️ Odds U2.5': odds, '💰 แนะนำแทง (บาท)': f"{bet_amount:.0f} ฿"})
        if final_bets:
            df_bets = pd.DataFrame(final_bets); st.dataframe(df_bets, width="stretch", hide_index=True)
            excel_file = create_excel_with_formula(edited_df, bankroll)
            if excel_file:
                st.download_button(label="📥 ดาวน์โหลดไฟล์ Excel", data=excel_file, file_name=f'Under_Bet_{datetime.now().strftime("%Y-%m-%d")}.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)
        else: st.warning("กรุณาใส่เลข Odds ที่มากกว่า 1.50")

    if st.session_state.near_misses:
        st.divider(); st.warning(f"⚠️ คู่ที่ใกล้เคียงเกณฑ์")
        df_near = pd.DataFrame(st.session_state.near_misses); df_near = df_near.sort_values(by='คะแนน', ascending=False).head(3).reset_index(drop=True)
        st.dataframe(df_near, width="stretch", hide_index=True)

# ==========================================
# TAB 2
# ==========================================
with tab2:
    st.markdown("### 📈 ระบบทดสอบย้อนหลัง (Backtesting)")
    st.caption("ระบบจะย้อนไปดูเกมเมื่อวานที่จบแล้ว และคำนวณว่า 'ถ้าเราแทงทุกคู่ที่ผ่านเกณฑ์ จะได้กำไรหรือเสียเงิน'")
    
    col1, col2 = st.columns(2)
    with col1:
        back_score = st.slider("🎯 คะแนนเกณฑ์ที่ใช้ทดสอบ", min_value=40, max_value=80, value=60, step=5, key="score_yest")
    with col2:
        back_xg = st.slider("📊 ค่า xG ที่ใช้ทดสอบ", min_value=2.0, max_value=3.5, value=2.6, step=0.1, key="xg_yest")
    back_bank = st.number_input("💰 เงินทุนสมมติฐาน (บาท)", min_value=100, value=5000, step=100, key="bank_yest")
    ASSUMED_ODDS = 1.70
    
    if st.button("🔄 เริ่มวิเคราะห์ผลเมื่อวาน", type="primary", use_container_width=True):
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        LIST_URL_YEST = f"https://api.sstats.net/games/list?date={yesterday_date}"
        STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}"
        
        with st.spinner(f'กำลังดึงข้อมูลเกมวันที่ {yesterday_date}...'):
            try:
                res_list = requests.get(LIST_URL_YEST, headers=HEADERS, timeout=10).json()
                games = res_list.get('data', [])
                games = [g for g in games if g.get('statusName', '').lower() == 'finished']
            except: games = []

        if not games:
            st.warning(f"ไม่พบเกมที่แข่งจบแล้วในวันที่ {yesterday_date}")
        else:
            st.info(f"พบ {len(games)} คู่ที่จบแล้ว กำลังวิเคราะห์...")
            results = []
            progress_text = st.empty(); progress_bar = st.progress(0)
            
            for index, g in enumerate(games):
                game_id = g.get('id'); home = g.get('homeTeam', {}).get('name', 'Home')
                away = g.get('awayTeam', {}).get('name', 'Away')
                home_ft = g.get('homeFTResult', 0) or 0; away_ft = g.get('awayFTResult', 0) or 0
                total_goals = int(home_ft) + int(away_ft)
                
                progress_text.text(f"ตรวจสอบ: {home} vs {away} (สกอร์ {home_ft}-{away_ft})")
                progress_bar.progress((index + 1) / len(games))
                
                try:
                    stats_url = STATS_URL_FORMAT.format(game_id)
                    res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=5)
                    if res_stats_req.status_code != 200: time.sleep(0.2); continue 
                    
                    res_stats = res_stats_req.json(); glicko_data = res_stats.get('data', {}).get('glicko', {})
                    home_xg = glicko_data.get('homeXg'); away_xg = glicko_data.get('awayXg')
                    if home_xg is None or away_xg is None: time.sleep(0.2); continue
                    
                    home_xg = float(home_xg); away_xg = float(away_xg); combined_xg = home_xg + away_xg
                    poisson_prob = calculate_poisson_under(home_xg, away_xg)
                    score = calculate_score(combined_xg, poisson_prob, back_xg)
                    
                    if score >= back_score:
                        is_win = total_goals <= 2
                        stake_pct, bet_amount = calculate_kelly_stake(ASSUMED_ODDS, poisson_prob, back_bank)
                        profit_loss = (bet_amount * (ASSUMED_ODDS - 1)) if is_win else -bet_amount
                        
                        results.append({
                            'ลีก': f"{g.get('season', {}).get('league', {}).get('country', {}).get('name', '')} - {g.get('season', {}).get('league', {}).get('name', 'Unknown')}",
                            'คู่บอล': f"{home} vs {away}",
                            'สกอร์จริง': f"{home_ft}-{away_ft} (รวม {total_goals})",
                            'เดิมพัน': f"{bet_amount:.0f} ฿",
                            'ผลลัพธ์': '✅ ได้' if is_win else '❌ เสีย',
                            'กำไร/ขาดทุน': f"{'+' if profit_loss > 0 else ''}{profit_loss:.0f} ฿"
                        })
                    time.sleep(0.3)
                except: time.sleep(0.5); continue
                    
            progress_bar.empty(); progress_text.empty()
            
            if results:
                st.divider()
                df_results = pd.DataFrame(results)
                
                total_pl = sum([float(str(p).replace(' ฿','').replace('+','')) for p in df_results['กำไร/ขาดทุน']])
                wins = len(df_results[df_results['ผลลัพธ์'] == '✅ ได้'])
                win_rate = (wins / len(df_results)) * 100
                roi = (total_pl / back_bank) * 100
                
                c1, c2, c3 = st.columns(3)
                c1.metric("คู่ที่แทง", len(df_results))
                c2.metric("Win Rate", f"{win_rate:.1f}%")
                c3.metric("ROI วันนี้", f"{roi:.2f}%", delta=f"{total_pl:.0f} ฿")
                
                st.dataframe(df_results, width="stretch", hide_index=True)
                
                if GSPREAD_AVAILABLE:
                    if st.button("💾 บันทึกผลลัพธ์นี้ลง Google Sheets", type="primary", use_container_width=True):
                        if save_to_google_sheet(results):
                            st.success("✅ บันทึกข้อมูลสำเร็จแล้ว! ลองเปิด Google Sheets ดูได้เลยครับ")
                        else:
                            st.error("เกิดข้อผิดพลาด ลองตรวจสอบ Secrets หรือสิทธิ์การแชร์ไฟล์")
                else:
                    st.info("ℹ️ ฟีเจอร์บันทึก Google Sheets ถูกปิดเพราะข้อจำกัดของเซิร์ฟเวอร์ฟรี (แนะนำให้ใช้ปุ่มดาวน์โหลด CSV แทน)")
            else:
                st.warning("เมื่อวานไม่มีคู่ไหนผ่านเกณฑ์เลยครับ")
