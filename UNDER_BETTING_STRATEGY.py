import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
from scipy.stats import poisson

# ==========================================
# ตั้งค่า API
# ==========================================
API_KEY = st.secrets.get("API_KEY", "ynl1sr2l6ljzaole") 
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

# ปรับฟังก์ชันให้รับค่า xG_target แบบ Dynamic
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
        return None
    except:
        return None

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

# ==========================================
# หน้าตาเว็บแอพ (UI)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")
st.title("🚀 UNDER BOT - Dynamic Edition")
st.markdown("ปรับเกณฑ์เองได้ตามสภาพแวดล้อมของตลาด")

# ==========================================
# 🌟 ส่วนใหม่: แถบปรับค่า (Sliders)
# ==========================================
with st.expander("⚙️ ตั้งค่าเกณฑ์การคัดกรอง (ลากเลื่อนเพื่อปรับ)", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        score_target = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ", min_value=40, max_value=80, value=60, step=5)
    with col2:
        xg_target = st.slider("📊 ค่า xG รวมสูงสุดที่ยอมรับ", min_value=2.0, max_value=3.5, value=2.6, step=0.1)
    
    bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100)
    st.caption("💡 *เคล็ดลับ: ถ้าวันไหนไม่เจอคู่เลย ให้ลองลดคะแนนลงมาเป็น 55 และขยับ xG ขึ้นเป็น 2.8 ดูครับ*")

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
    else:
        st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์ตามเกณฑ์ใหม่...")
        
        approved_matches = []
        near_misses = []
        
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
                
                # ส่ง xg_target ที่ผู้ใช้ปรับเข้าไป
                poisson_prob = calculate_poisson_under(home_xg, away_xg)
                score = calculate_score(combined_xg, poisson_prob, xg_target)
                
                u25_odds = get_under_25_odds(g)
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
                
                if score >= score_target:
                    approved_matches.append(match_data)
                elif score >= (score_target - 10): # ให้โชว์คู่ที่คะแนนต่ำกว่า 10 คะแนน ไว้ดูเป็นตัวเลือก
                    near_misses.append(match_data)
                    
                time.sleep(0.5)
                
            except Exception:
                time.sleep(1)
                continue
                
        progress_bar.empty()
        progress_text.empty()
        
        # ==========================================
        # แสดงผลลัพธ์หลัก
        # ==========================================
        if approved_matches:
            st.divider()
            st.success(f"🎯 พบ **{len(approved_matches)} คู่** ที่ผ่านเกณฑ์ (คะแนน > {score_target})")
            
            df = pd.DataFrame(approved_matches)
            df = df.sort_values(by='คะแนน (Score)', ascending=False).reset_index(drop=True)
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
            
            # เพิ่มปุ่มดาวน์โหลด CSV
            @st.cache_data
            def convert_df(df):
                # ใช้ utf-8-sig เพื่อให้ภาษาไทยไม่เพี้ยนตอนเปิดด้วย Excel
                return df.to_csv(index=False).encode('utf-8-sig')

            csv_data = convert_df(display_df)
            st.download_button(
                label="📥 ดาวน์โหลดข้อมูลคู่เด่นวันนี้ (.csv)",
                data=csv_data,
                file_name=f'Under_Bot_{datetime.now().strftime("%Y-%m-%d")}.csv',
                mime='text/csv',
                use_container_width=True
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
        # แสดง Near Misses (ตัวเลือกเสริม)
        # ==========================================
        if near_misses:
            st.divider()
            st.warning(f"⚠️ คู่ที่ใกล้เคียงเกณฑ์ (คะแนน {score_target - 10} - {score_target - 1})")
            
            df_near = pd.DataFrame(near_misses)
            df_near = df_near.sort_values(by='คะแนน (Score)', ascending=False).head(3).reset_index(drop=True)
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
            st.info("💡 *คู่เหล่านี้ใช้เกณฑ์ไม่ถึง 100% ถ้าจะแทงขอแนะนำให้ลดเงินลงครึ่งหนึ่ง*")
            
        if not approved_matches and not near_misses:
            st.divider()
            st.error("❌ วันนี้ไม่มีคู่ไหนใกล้เคียงเกณฑ์เลย ขอแนะนำให้ 'พักเงิน' วันนี้ครับ")

st.caption("Developed for Under Strategy v2.0 | Data from sstats.net")
