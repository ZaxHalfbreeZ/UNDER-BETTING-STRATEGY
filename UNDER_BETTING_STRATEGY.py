import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
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

def calculate_score(combined_xg, poisson_under):
    norm_xg = max(0, 1 - (combined_xg / 2.3)) if combined_xg < 2.3 else 0
    norm_poisson = min(1.0, poisson_under / 52) if poisson_under > 52 else 0
    score = (norm_xg * 40) + (norm_poisson * 60)
    return round(score, 1)

def get_under_25_odds(game_data):
    """ฟังก์ชันสำหรับดึงราคาน้ำ Under 2.5 จากข้อมูล JSON"""
    try:
        odds_list = game_data.get('odds', [])
        for market in odds_list:
            market_name = market.get('marketName', '').lower()
            if '2.5' in market_name or 'under/over' in market_name:
                for odd in market.get('odds', []):
                    if 'under' in odd.get('name', '').lower():
                        val = odd.get('value')
                        # ถ้าราคาน้ำต่ำกว่า 1.50 ให้ถือว่าไม่มีค่า (ตามสูตร)
                        if val and val >= 1.50:
                            return val
        return None
    except:
        return None

def calculate_kelly_stake(odds, probability, bankroll):
    """คำนวณเงินแทงตามสูตร Fractional Kelly (30%) จำกัดไว้แค่ 5%"""
    if not odds or odds <= 1.0 or not bankroll:
        return 0.0, 0.0
    
    b = odds - 1
    p = probability / 100.0
    q = 1 - p
    
    kelly = (b * p - q) / b
    fractional_kelly = kelly * 0.30 # ใช้ 30% ของ Kelly เต็ม
    stake_pct = max(0, fractional_kelly * 100)
    stake_pct = min(stake_pct, 5.0) # จำกัดสูงสุด 5% ของเงินทุนตาม PDF
    
    bet_amount = bankroll * (stake_pct / 100)
    return round(stake_pct, 1), round(bet_amount)

# ==========================================
# หน้าตาเว็บแอพ (UI)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")
st.title("🚀 UNDER BETTING STRATEGY v2.0 - Pro Edition")
st.markdown("ระบบคัดกรอง + คำนวณเงินแทงอัตโนมัติ (xG + Poisson + Kelly)")

# ช่องกรอกเงินทุน
col1, col2 = st.columns(2)
with col1:
    bankroll = st.number_input("💰 กรอกเงินทุนทั้งหมดของคุณ (บาท)", min_value=100, value=5000, step=100)
with col2:
    st.info("ระบบจะคำนวณเงินแทงแบบ Fractional Kelly (30%)\nโดยจำกัดไม่เกิน 5% ต่อคู่")

if st.button("🔍 เริ่มค้นหาและคำนวณคู่เกมวันนี้", type="primary", use_container_width=True):
    
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
    else:
        st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์ xG, Odds และคำนวณเงินแทงทีละคู่...")
        
        approved_matches = []
        near_misses = [] # สำหรับเก็บคู่ที่ใกล้เคียงเกณฑ์
        
        progress_text = st.empty()
        progress_bar = st.progress(0)
        
        for index, g in enumerate(games):
            game_id = g.get('id')
            league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
            home = g.get('homeTeam', {}).get('name', 'Home')
            away = g.get('awayTeam', {}).get('name', 'Away')
            
            progress_text.text(f"กำลังตรวจสอบ: {home} vs {away}")
            progress_bar.progress((index + 1) / len(games))

            try:
                # 1. ดึง xG
                stats_url = STATS_URL_FORMAT.format(game_id)
                res_stats_req = requests.get(stats_url, headers=HEADERS, timeout=5)
                
                if res_stats_req.status_code != 200:
                    time.sleep(0.3)
                    continue 
                    
                res_stats = res_stats_req.json()
                glicko_data = res_stats.get('data', {}).get('glicko', {})
                home_xg = glicko_data.get('homeXg')
                away_xg = glicko_data.get('awayXg')
                
                if home_xg is None or away_xg is None:
                    time.sleep(0.3)
                    continue
                    
                home_xg = float(home_xg)
                away_xg = float(away_xg)
                combined_xg = home_xg + away_xg
                
                # 2. คำนวณ Poisson & Score
                poisson_prob = calculate_poisson_under(home_xg, away_xg)
                score = calculate_score(combined_xg, poisson_prob)
                
                # 3. ดึง Odds จากข้อมูลเกม (ไม่ต้องยิง API เพิ่ม)
                u25_odds = get_under_25_odds(g)
                
                # 4. คำนวณ Kelly (ใช้ได้กับทั้ง Approved และ Near Miss)
                stake_pct, bet_amount = calculate_kelly_stake(u25_odds, poisson_prob, bankroll)
                
                match_data = {
                    'ลีก': league,
                    'ทีมเหย้า': home,
                    'ทีมเยือน': away,
                    'xG รวม': combined_xg,
                    'Poisson U2.5 (%)': poisson_prob,
                    'Odds U2.5': u25_odds if u25_odds else "-",
                    'สัดส่วนแทง (%)': stake_pct if u25_odds else 0.0,
                    '💰 แนะนำแทง (บาท)': bet_amount if u25_odds else 0.0,
                    'คะแนน (Score)': score
                }
                
                # แยกเก็บข้อมูล
                if score >= 70:
                    approved_matches.append(match_data)
                elif score >= 50: # คู่ที่ใกล้เคียง (50-69 คะแนน)
                    near_misses.append(match_data)
                    
                time.sleep(0.5)
                
            except Exception:
                time.sleep(1)
                continue
                
        progress_bar.empty()
        progress_text.empty()
        
        # ==========================================
        # แสดงผลลัพธ์หลัก (คะแนน > 70)
        # ==========================================
        if approved_matches:
            st.divider()
            st.success(f"🎯 พบ **{len(approved_matches)} คู่** ที่ผ่านเกณฑ์สูงสุด! พร้อมแทง")
            
            df = pd.DataFrame(approved_matches)
            df = df.sort_values(by='คะแนน (Score)', ascending=False).reset_index(drop=True)
            
            # จัดรูปแบบการแสดงผล
            display_df = df.copy()
            display_df['Odds U2.5'] = display_df['Odds U2.5'].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x)
            
            st.dataframe(
                display_df.style.format({
                    'xG รวม': '{:.2f}',
                    'Poisson U2.5 (%)': '{:.1f}%',
                    'สัดส่วนแทง (%)': '{:.1f}%',
                    '💰 แนะนำแทง (บาท)': '{:.0f} ฿',
                    'คะแนน (Score)': '{:.1f}'
                }).background_gradient(subset=['คะแนน (Score)', 'Poisson U2.5 (%)'], cmap='Greens'),
                use_container_width=True,
                height=400
            )
            
            # คู่มือ Step-by-Step (เหมือนเดิม)
            with st.expander("👇 กดอ่านขั้นตอนการจับ Trigger ทีละ Step", expanded=True):
                st.markdown("""
                **🟢 ขั้นตอนที่ 1: เตรียมตัวก่อนเกม**
                > 1. เปิดแอป SofaScore หรือ FlashScore ค้นหาชื่อทีมจากตารางด้านบน
                > 2. เปิดเว็บพนันเข้าไปที่หน้าแมตช์นั้น เตรียมพร้อม
                
                **🔴 ขั้นตอนที่ 2: รอจังหวะเข้า (นาทีที่ 35)**
                > - [ ] สกอร์ ยังเป็น **0:0** หรือ **1:0** อยู่ไหม?
                > - [ ] สถิติ **Dangerous Attacks** รวมทั้งสนาม **น้อยกว่า 20 ครั้ง** ไหม?
                > - [ ] เกมดูเชยชม ไม่มีจังหวะสุดวิสัย?
                
                **💰 ขั้นตอนที่ 3: กดแทง!**
                > ถ้าเช็คถูกทุกข้อ ให้กดแทง **Under 2.5** หรือ **Under 3.5** (ถ้าสกอร์ 1:0) 
                > ตามจำนวนเงินในคอลัมน์ **"💰 แนะนำแทง"** ที่ระบบคำนวณไว้ให้เลย!
                """)
                
        # ==========================================
        # แสดงผล Near Misses (คะแนน 50 - 69)
        # ==========================================
        if not approved_matches and near_misses:
            st.divider()
            st.warning("⚠️ วันนี้ไม่มีคู่ที่ผ่านเกณฑ์เยี่ยม (>70) แต่มีคู่ที่ใกล้เคียงด้านล่างนี้ให้พิจารณา")
            
            df_near = pd.DataFrame(near_misses)
            df_near = df_near.sort_values(by='คะแนน (Score)', ascending=False).head(5).reset_index(drop=True)
            
            display_near = df_near.copy()
            display_near['Odds U2.5'] = display_near['Odds U2.5'].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x)
            
            st.dataframe(
                display_near.style.format({
                    'xG รวม': '{:.2f}',
                    'Poisson U2.5 (%)': '{:.1f}%',
                    'สัดส่วนแทง (%)': '{:.1f}%',
                    '💰 แนะนำแทง (บาท)': '{:.0f} ฿',
                    'คะแนน (Score)': '{:.1f}'
                }).background_gradient(subset=['คะแนน (Score)'], cmap='YlOrRd'),
                use_container_width=True
            )
            st.info("💡 *คู่เหล่านี้ผ่านเกณฑ์ไม่ถึง 100% แต่ถ้าคุณมองด้วยตาเองว่าเกมดู Under ชัดเจน ก็สามารถรับความเสี่ยงเองได้ แต่ขอแนะนำให้ลดเงินแทงลงครึ่งหนึ่ง*")
            
        elif not approved_matches and not near_misses:
            st.divider()
            st.error("❌ วันนี้สนามร้อนแน่! ไม่มีคู่ไหนเหมาะกับสูตรเลย ขอแนะนำให้ "พักเงิน" วันนี้ครับ")

st.caption("Developed for Under Strategy v2.0 | Data from sstats.net | Kelly Criterion Calculator Included")
