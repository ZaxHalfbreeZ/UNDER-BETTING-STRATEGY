import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

# ==========================================
# ตั้งค่า API
# ==========================================
API_KEY = st.secrets["API_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}", 
    "Accept": "application/json"
}
LIST_URL = f"https://api.sstats.net/games/list?date={datetime.now().strftime('%Y-%m-%d')}"
STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}" 

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
        thai_tz = timezone(timedelta(hours=4))
        dt_thai = dt.astimezone(thai_tz)
        return dt_thai.strftime("%H:%M")
    except:
        return "N/A"

# ==========================================
# ตั้งค่าความจำของระบบ (Session State)
# ==========================================
if 'approved_matches' not in st.session_state:
    st.session_state.approved_matches = []
if 'near_misses' not in st.session_state:
    st.session_state.near_misses = []

# ==========================================
# หน้าตาเว็บแอพ (UI)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")
st.title("🚀 UNDER BOT - Dynamic Edition")

with st.expander("⚙️ ตั้งค่าเกณฑ์การคัดกรอง", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        score_target = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ", min_value=40, max_value=80, value=60, step=5)
    with col2:
        xg_target = st.slider("📊 ค่า xG รวมสูงสุดที่ยอมรับ", min_value=2.0, max_value=3.5, value=2.6, step=0.1)
    bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100)

if st.button("🔍 เริ่มค้นหาคู่เกมวันนี้", type="primary", use_container_width=True):
    
    with st.spinner('กำลังดึงรายการแมตช์...'):
        try:
            res_list = requests.get(LIST_URL, headers=HEADERS, timeout=10).json()
            games = res_list.get('data', [])
            games = [g for g in games if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'postponed']]
        except:
            st.error("❌ ไม่สามารถเชื่อมต่อ API ได้")
            games = []

    if not games:
        st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้")
        st.session_state.approved_matches = []
        st.session_state.near_misses = []
    else:
        st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์ตามเกณฑ์ใหม่...")
        
        temp_approved = []
        temp_near = []
        
        progress_text = st.empty()
        progress_bar = st.progress(0)
        
        for index, g in enumerate(games):
            game_id = g.get('id')
            league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
            home = g.get('homeTeam', {}).get('name', 'Home')
            away = g.get('awayTeam', {}).get('name', 'Away')
            raw_date = g.get('date') 
            
            progress_text.text(f"กำลังตรวจสอบ: {home} vs {away}")
            progress_bar.progress((index + 1) / len(games))

            try:
                stats_url = STATS_URL_FORMAT.format(game_id)
                res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=5)
                if res_stats_req.status_code != 200:
                    time.sleep(0.3); continue 
                    
                res_stats = res_stats_req.json()
                glicko_data = res_stats.get('data', {}).get('glicko', {})
                home_xg = glicko_data.get('homeXg')
                away_xg = glicko_data.get('awayXg')
                
                if home_xg is None or away_xg is None:
                    time.sleep(0.3); continue
                    
                home_xg = float(home_xg)
                away_xg = float(away_xg)
                combined_xg = home_xg + away_xg
                
                poisson_prob = calculate_poisson_under(home_xg, away_xg)
                # ✅ แก้ไข Typo ตรงนี้แล้วครับ (จาก poisson_under เป็น poisson_prob)
                score = calculate_score(combined_xg, poisson_prob, xg_target)
                
                u25_odds = get_under_25_odds(g)
                
                match_data = {
                    '⏰ เวลา': format_match_time(raw_date),
                    '🏆 ลีก': league,
                    'ทีมเหย้า': home,
                    'ทีมเยือน': away,
                    'xG รวม': combined_xg,
                    'Poisson U2.5 (%)': poisson_prob,
                    'คะแนน': score,
                    '✏️ Odds U2.5': u25_odds 
                }
                
                if score >= score_target:
                    temp_approved.append(match_data)
                elif score >= (score_target - 10): 
                    temp_near.append(match_data)
                    
                time.sleep(0.5)
            except Exception:
                time.sleep(1); continue
                
        progress_bar.empty()
        progress_text.empty()
        
        # เก็บข้อมูลลงความจำ
        st.session_state.approved_matches = temp_approved
        st.session_state.near_misses = temp_near

# ==========================================
# ส่วนแสดงผลลัพธ์
# ==========================================
if st.session_state.approved_matches:
    st.divider()
    st.success(f"🎯 พบ **{len(st.session_state.approved_matches)} คู่** ที่ผ่านเกณฑ์!")
    st.caption("💡 *ถ้าช่อง Odds เป็น 0.00 ให้แตะที่ตัวเลขแล้วพิมพ์ราคาน้ำจากเว็บพนันลงไป เงินแทงจะคำนวณให้อัตโนมัติ*")
    
    df = pd.DataFrame(st.session_state.approved_matches)
    df = df.sort_values(by='คะแนน', ascending=False).reset_index(drop=True)
    
    # ✅ เปลี่ยน use_container_width=True เป็น width="stretch" ตาม Streamlit เวอร์ชันใหม่
    edited_df = st.data_editor(
        df,
        disabled=["⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน", "xG รวม", "Poisson U2.5 (%)", "คะแนน"],
        width="stretch", 
        height=400,
        hide_index=True
    )
    
    # ==========================================
    # คำนวณเงินแทง
    # ==========================================
    st.subheader("💰 สรุปเงินแทงสุดท้าย")
    
    final_bets = []
    for index, row in edited_df.iterrows():
        odds = row['✏️ Odds U2.5']
        prob = row['Poisson U2.5 (%)']
        
        if odds >= 1.50: 
            stake_pct, bet_amount = calculate_kelly_stake(odds, prob, bankroll)
            if bet_amount > 0:
                final_bets.append({
                    'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                    '✏️ Odds U2.5': odds,
                    '💰 แนะนำแทง (บาท)': f"{bet_amount:.0f} ฿"
                })
    
    if final_bets:
        df_bets = pd.DataFrame(final_bets)
        st.dataframe(df_bets, width="stretch", hide_index=True)
        
        @st.cache_data
        def convert_df(df):
            return df.to_csv(index=False).encode('utf-8-sig')
        csv_data = convert_df(df_bets)
        st.download_button(
            label="📥 ดาวน์โหลดตารางนี้ (.csv)",
            data=csv_data,
            file_name=f'Under_Bet_{datetime.now().strftime("%Y-%m-%d")}.csv',
            mime='text/csv',
            use_container_width=True
        )
    else:
        st.warning("กรุณาใส่เลข Odds ที่มากกว่า 1.50 ในตารางด้านบน เพื่อให้ระบบคำนวณเงินแทงให้")

    with st.expander("👇 ขั้นตอนการจับ Trigger", expanded=True):
        st.markdown("""
        **🔴 รอจังหวะเข้า (นาทีที่ 35)**
        > - [ ] สกอร์ ยังเป็น **0:0** หรือ **1:0** อยู่ไหม?
        > - [ ] สถิติ **Dangerous Attacks** รวมทั้งสนาม **น้อยกว่า 20 ครั้ง** ไหม?
        > - [ ] เกมดูเชยชม ไม่มีจังหวะสุดวิสัย?
        
        **💰 กดแทงตามจำนวนเงินในตารางด้านบน!**
        """)
        
# ==========================================
# แสดง Near Misses
# ==========================================
if st.session_state.near_misses:
    st.divider()
    st.warning(f"⚠️ คู่ที่ใกล้เคียงเกณฑ์ (คะแนน {score_target - 10} - {score_target - 1})")
    df_near = pd.DataFrame(st.session_state.near_misses)
    df_near = df_near.sort_values(by='คะแนน', ascending=False).head(3).reset_index(drop=True)
    st.dataframe(df_near, width="stretch", hide_index=True)
    st.info("💡 *คู่เหล่านี้ใช้เกณฑ์ไม่ถึง 100% ถ้าจะแทงขอแนะนำให้ลดเงินลงครึ่งหนึ่ง*")

if not st.session_state.approved_matches and not st.session_state.near_misses:
    st.divider()
    st.error("❌ ยังไม่มีข้อมูล กรุณากดปุ่ม 'เริ่มค้นหาคู่เกมวันนี้' ด้านบน")
