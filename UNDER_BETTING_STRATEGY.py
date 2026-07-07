import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
from scipy.stats import poisson

# ==========================================
# ตั้งค่า API
# ==========================================
# ถ้ารันบน Cloud ให้ใช้ st.secrets ถ้ารันบนคอมให้ใส่เลข API Key ตรงนี้แทน st.secrets
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

# ==========================================
# หน้าตาเว็บแอพ (UI)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")
st.title("🚀 UNDER BETTING STRATEGY v2.0")
st.markdown("ระบบคัดกรองคู่เกม 'Under' ด้วย xG + Poisson Distribution")

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
        st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์ xG ทีละคู่ (ใช้เวลาประมาณ 30-45 วินาที)...")
        
        approved_matches = []
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
                
                poisson_prob = calculate_poisson_under(home_xg, away_xg)
                score = calculate_score(combined_xg, poisson_prob)
                
                if score >= 70:
                    approved_matches.append({
                        'ลีก': league,
                        'ทีมเหย้า': home,
                        'ทีมเยือน': away,
                        'xG เหย้า': home_xg,
                        'xG เยือน': away_xg,
                        'xG รวม': combined_xg,
                        'ความน่าจะเป็น U2.5 (%)': poisson_prob,
                        'คะแนน (Score)': score
                    })
                    
                time.sleep(0.5)
                
            except Exception:
                time.sleep(1)
                continue
                
        progress_bar.empty()
        progress_text.empty()
        
        # ==========================================
        # แสดงผลลัพธ์ + คู่มือการใช้งาน
        # ==========================================
        if approved_matches:
            st.divider()
            st.success(f"🎯 พบ **{len(approved_matches)} คู่** ที่ผ่านเกณฑ์! มีแนวโน้ม Under สูง")
            
            df = pd.DataFrame(approved_matches)
            df = df.sort_values(by='คะแนน (Score)', ascending=False).reset_index(drop=True)
            
            st.dataframe(
                df.style.format({
                    'xG เหย้า': '{:.2f}',
                    'xG เยือน': '{:.2f}',
                    'xG รวม': '{:.2f}',
                    'ความน่าจะเป็น U2.5 (%)': '{:.1f}%',
                    'คะแนน (Score)': '{:.1f}'
                }).background_gradient(subset=['คะแนน (Score)', 'ความน่าจะเป็น U2.5 (%)'], cmap='Greens'),
                use_container_width=True,
                height=400
            )
            
            # ==========================================
            # 🌟 ส่วนเพิ่มใหม่: คู่มือ Step-by-Step สำหรับมือถือ
            # ==========================================
            st.divider()
            st.subheader("📋 คู่มือการทำงาน (Semi-Auto Workflow)")
            st.markdown("""
            เมื่อพบคู่ที่น่าสนใจจากตารางด้านบน ให้ทำตามขั้นตอนนี้เพื่อสร้างกำไร:
            """)
            
            # ใช้ expander แบบเปิดไว้เลย เพื่อให้เห็นขั้นตอนทันทีบนมือถือ
            with st.expander("👇 กดอ่านขั้นตอนการจับ Trigger ทีละ Step", expanded=True):
                
                st.markdown("""
                **🟢 ขั้นตอนที่ 1: เตรียมตัวก่อนเกม**
                > 1. เปิดแอป SofaScore หรือ FlashScore บนมือถือ
                > 2. ค้นหาชื่อทีมที่ได้จากตารางด้านบน แล้วกด "เพิ่มในรายการโปรด" เพื่อไม่ให้หาย
                > 3. เปิดเว็บพนันที่คุณใช้อยู่ แล้วเข้าไปที่หน้าแมตช์นั้น (เตรียมไว้ก่อน)
                
                **🔴 ขั้นตอนที่ 2: รอจังหวะเข้า (สำคัญที่สุด)**
                > *อย่าเพิ่งแทงก่อนเกมเริ่ม!* ให้รอดูเกมจนถึง **นาทีที่ 35** แล้วตรวจสอบเงื่อนไขต่อไปนี้ที่หน้าจอ SofaScore:
                > - [ ] สกอร์ ยังเป็น **0:0** หรือ **1:0** อยู่ไหม? (ถ้าเกิน 1 ประตู ข้ามไปเลย)
                > - [ ] สถิติ **Dangerous Attacks (จังหวะบุกอันตราย)** รวมทั้งสนาม **น้อยกว่า 20 ครั้ง** ไหม?
                > - [ ] ดูจาก "สายตา" ของคุณเอง: เกมดูเชยชม เป็นบอลกลางสนาม ไม่มีจังหวะสุดวิสัย?
                
                **💰 ขั้นตอนที่ 3: กดแทง!**
                > ถ้าเช็คลิสต์ด้านบนแล้ว **"ถูกทุกข้อ"** ให้ทำทันที:
                > 1. กดเลือกตลาด **Under 2.5** หรือ **Under 3.5** (ถ้าสกอร์ 1:0 อยู่ แนะนำ Under 3.5)
                > 2. ตรวจสอบราคาน้ำ (Odds) ต้องไม่ต่ำกว่า **1.50**
                > 3. กำหนดเงินเดิมพัน: **ใช้เพียง 2% - 5% ของเงินทุน** เท่านั้น (อย่าโลภ)
                > 4. กดยืนยันรายการ แล้วปิดแอปพนัน เพลิดเพลินดูบอลต่อ!
                """)
                
        else:
            st.divider()
            st.warning("❌ วันนี้ไม่มีคู่ไหนผ่านเกณฑ์ Score > 70 ครับ (อย่าบังคับแทง รอโอกาสที่ดีกว่านี้)")

st.caption("Developed for Under Strategy v2.0 | Data from sstats.net")
