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
# ฟังก์ชันคำนวณ (ตามสูตร PDF v2.0)
# ==========================================
def calculate_poisson_under(lambda_home, lambda_away):
    under_prob = 0
    for i in range(3):
        for j in range(3 - i):
            under_prob += poisson.pmf(i, lambda_home) * poisson.pmf(j, lambda_away)
    return under_prob * 100

def calculate_score(combined_xg, poisson_under):
    """ปรับน้ำหนักการให้คะแนน เน้น xG 40% และ Poisson 60%"""
    norm_xg = max(0, 1 - (combined_xg / 2.3)) if combined_xg < 2.3 else 0
    norm_poisson = min(1.0, poisson_under / 52) if poisson_under > 52 else 0
    score = (norm_xg * 40) + (norm_poisson * 60)
    return round(score, 1)

# ==========================================
# หน้าตาเว็บแอพ (UI)
# ==========================================
st.set_page_config(page_title="Under Bot v2.0", page_icon="🚀", layout="wide")
st.title("🚀 UNDER BETTING STRATEGY v2.0 - Pre-Match Scanner")
st.markdown("ระบบคัดกรองคู่เกมที่มีแนวโน้ม 'Under' สูง ก่อนเกมเริ่ม (ใช้ xG + Poisson)")

# ปุ่มกดเริ่มวิเคราะห์
if st.button("🔍 เริ่มค้นหาคู่เกมวันนี้", type="primary", use_container_width=True):
    
    with st.spinner('กำลังดึงรายการแมตช์...'):
        try:
            res_list = requests.get(LIST_URL, headers=HEADERS, timeout=10).json()
            games = res_list.get('data', [])
            # กรองเอาแค่เกมที่ยังไม่เล่น
            games = [g for g in games if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'postponed']]
        except:
            st.error("❌ ไม่สามารถเชื่อมต่อ API เพื่อดึงรายการแมตช์ได้")
            games = []

    if not games:
        st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้")
    else:
        st.info(f"พบ {len(games)} คู่ กำลังวิเคราะห์ xG ทีละคู่ (ใช้เวลาประมาณ 30-45 วินาที)...")
        
        approved_matches = []
        
        # สร้าง Progress Bar เพื่อให้รู้ว่าโปรแกรมไม่ได้หยุดนิ่ง
        progress_text = st.empty()
        progress_bar = st.progress(0)
        
        for index, g in enumerate(games):
            game_id = g.get('id')
            league = g.get('season', {}).get('league', {}).get('name', 'Unknown')
            home = g.get('homeTeam', {}).get('name', 'Home')
            away = g.get('awayTeam', {}).get('name', 'Away')
            
            # อัพเดท Progress Bar
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
                    
                time.sleep(0.5) # หน่วงเวลาไม่ให้โดนแบน
                
            except Exception:
                time.sleep(1)
                continue
                
        # ซ่อน Progress Bar เมื่อเสร็จแล้ว
        progress_bar.empty()
        progress_text.empty()
        
        # ==========================================
        # แสดงผลลัพธ์สุดท้าย
        # ==========================================
        if approved_matches:
            st.divider()
            st.success(f"🎯 พบ **{len(approved_matches)} คู่** ที่ผ่านเกณฑ์ Score > 70 (เหมาะสำหรับจับตา!")
            
            df = pd.DataFrame(approved_matches)
            # เรียงลำดับตามคะแนนมากไปน้อย
            df = df.sort_values(by='คะแนน (Score)', ascending=False).reset_index(drop=True)
            
            # แสดงตารางสวยๆ (ซ่อน index และจัดตัวเลขให้เป็นระเบียบ)
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
            
            st.markdown("""
            ---
            **📝 คู่มือการใช้งาน (Semi-Auto Workflow):**
            1. เปิดโปรแกรมนี้ก่อนแข่ง 30 นาที - 1 ชั่วโมง
            2. จดชื่อคู่ที่ได้มาจากตารางด้านบนไว้ (เฉพาะคู่ที่คะแนนสูงสุด)
            3. เปิด SofaScore หรือ FlashScore ไว้ดูสด
            4. **รอจนถึงนาทีที่ 35** แล้วใช้สายตาประเมินเองตาม Trigger #1:
               - สกอร์ยัง 0:0 หรือ 1:0 ?
               - ดูจากสถิติสดบน FlashScore: Dangerous Attacks น้อยกว่า 20 ครั้ง ?
               - ดูจากสายตา: เกมดูเชยชม ไม่มีจังหวะสุดวิสัย ?
               - **ถ้าใช่ทุกข้อ -> ให้แทง Under 2.5 หรือ Under 3.5 ทันที!**
            """)
            
        else:
            st.divider()
            st.warning("❌ วันนี้ไม่มีคู่ไหนผ่านเกณฑ์ Score > 70 ครับ (ไม่แนะนำให้แทง Under วันนี้)")

st.caption("Developed for Under Strategy v2.0 | Data from sstats.net")