"""
⚽ UNDER / OVER BOT v4.1 — Multi-Line Value Scanner
รันด้วย: streamlit run under_over_bot_v4.py
ต้องมีไฟล์ .streamlit/secrets.toml ที่มี API_KEY = "..." (เหมือน v3)
Dependencies: streamlit, requests, pandas, scipy, xlsxwriter

v4.1: ไม่ตรึงเส้น 2.5 — สแกนทุกเส้น Under/Over ที่ตลาดเปิดราคา (1.5, 2.5, 3.5, ...)
แล้วเลือกเส้นที่ให้ EV สูงสุดของแต่ละคู่ คัดเฉพาะคู่ที่มีราคาตลาดจริงยืนยัน
"""
import streamlit as st
import requests
import pandas as pd
import time
import io
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

st.set_page_config(page_title="Under/Over Bot v4.1 Multi-Line", page_icon="⚽", layout="wide")

# ==========================================
# 🎨 ธีมหน้าเว็บ (Modern UI)
# ==========================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Kanit:wght@300;400;500;600&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Kanit', 'Segoe UI', sans-serif; }
.hero { background: linear-gradient(135deg, #0f2027 0%, #203a43 55%, #2c5364 100%);
        padding: 1.5rem 2rem; border-radius: 18px; margin-bottom: 1.2rem;
        box-shadow: 0 8px 24px rgba(0,0,0,.18); }
.hero-title { color: #fff; font-size: 1.9rem; font-weight: 600; letter-spacing: .5px; }
.hero-title .ver { background: #ffb300; color: #111; font-size: .72rem; font-weight: 600;
                   padding: 3px 12px; border-radius: 99px; vertical-align: middle; margin-left: 10px; }
.hero-sub { color: rgba(255,255,255,.75); margin-top: .35rem; font-size: .95rem; }
.side-header { padding: .55rem 1.1rem; border-radius: 12px; color: #fff; font-weight: 600;
               font-size: 1.05rem; margin: .6rem 0 .1rem 0; }
.side-header.under { background: linear-gradient(90deg, #1e3c72, #2a5298); }
.side-header.over  { background: linear-gradient(90deg, #ad5300, #e67700); }
.side-header.both  { background: linear-gradient(90deg, #0f2027, #2c5364); }
[data-testid="stMetric"] { background: rgba(130,130,150,.08); border: 1px solid rgba(130,130,150,.22);
                           border-radius: 14px; padding: 14px 16px; }
footer { visibility: hidden; }
</style>""", unsafe_allow_html=True)

SIDE_LABEL = {'under': '🔽 Under 2.5', 'over': '🔼 Over 2.5'}
MODE_LABEL = {'under': '🔽 Under 2.5 เท่านั้น', 'over': '🔼 Over 2.5 เท่านั้น', 'both': '⚔️ สแกนทั้งสองฝั่ง'}

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
                  bet_type TEXT DEFAULT 'under', line REAL DEFAULT 2.5,
                  ev REAL DEFAULT 0, prob_cons REAL DEFAULT 0,
                  status TEXT DEFAULT 'pending', profit REAL DEFAULT 0)''')
    conn.commit()
    conn.close()

def migrate_db():
    # ไฟล์ .db จาก v3 ยังไม่มีคอลัมน์ bet_type — CREATE TABLE IF NOT EXISTS ไม่เติมให้ ต้อง ALTER เอง
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    # คอลัมน์ที่เพิ่มใน v4: bet_type (under/over), line (เส้นที่แทง), ev, prob_cons (ความน่าจะเป็นแบบปรับความเสี่ยงแล้ว)
    for stmt in ("ALTER TABLE pending_bets ADD COLUMN bet_type TEXT DEFAULT 'under'",
                 "ALTER TABLE pending_bets ADD COLUMN ev REAL DEFAULT 0",
                 "ALTER TABLE pending_bets ADD COLUMN prob_cons REAL DEFAULT 0",
                 "ALTER TABLE pending_bets ADD COLUMN line REAL DEFAULT 2.5"):
        try:
            c.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # มีคอลัมน์นี้อยู่แล้ว
    conn.close()

def save_bets_to_db(bets, scan_date):
    if not bets: return
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    for b in bets:
        c.execute('''INSERT INTO pending_bets
                     (scan_date, game_id, home, away, league, xg, poisson, score, odds, stake, bet_type, line, ev, prob_cons)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (scan_date, b['game_id'], b['ทีมเหย้า'], b['ทีมเยือน'], b['🏆 ลีก'],
                   b['xG รวม'], b['Poisson (%)'], b['คะแนน'], b['✏️ Odds'], b['stake_amount'], b['bet_type'],
                   b['line'], b['EV (%)'], b['P ปลอดภัย (%)']))
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
migrate_db()

# ==========================================
# ตั้งค่า API
# ==========================================
API_KEY = st.secrets["API_KEY"]
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

# ==========================================
# ฟังก์ชันคำนวณ
# ==========================================
def _clamp01(x):
    return max(0.0, min(1.0, x))

def under_probability(lambda_home, lambda_away, goals=2, stress=0.0):
    # ผลรวมของ Poisson สองตัว = Poisson(ผลรวม xG): เส้น L คือ P(ยิงรวมกันไม่เกิน floor(L) ประตู)
    total = (lambda_home + lambda_away) * (1.0 + stress)
    return float(poisson.cdf(goals, total) * 100)

def evaluate_line(side, line, lambda_home, lambda_away, odds, model_trust, stress_pct):
    """
    ประเมินเส้นเดียวของคู่หนึ่ง (side = 'under'/'over', line = เส้นครึ่ง เช่น 3.5):
      p_raw    : ความน่าจะเป็นดิบจาก Poisson ที่เส้นนั้น
      p_cons   : ค่าที่ใช้ตัดสินจริง — บีบเข้าหา 50% ตาม model_trust (กันโมเดลพลาด)
      stress_ok: แม้ xG คลาดเคลื่อนทางร้ายตาม stress_pct ก็ยังชนะเส้นคุ้มทุนของราคานี้
      ev       : Expected Value = p_cons × ราคาจริง − 1
    """
    stress = stress_pct / 100.0
    goal_limit = int(line)  # เส้นครึ่ง เช่น 3.5 → under คือยิงรวม ≤ 3, over คือยิงรวม ≥ 4
    if side == 'under':
        p_raw = under_probability(lambda_home, lambda_away, goals=goal_limit)
        p_stress = under_probability(lambda_home, lambda_away, goals=goal_limit, stress=+stress)
    else:
        p_raw = 100.0 - under_probability(lambda_home, lambda_away, goals=goal_limit)
        p_stress = 100.0 - under_probability(lambda_home, lambda_away, goals=goal_limit, stress=-stress)

    p_cons = 50.0 + (p_raw - 50.0) * model_trust
    p_cons_stress = 50.0 + (p_stress - 50.0) * model_trust
    ev = (p_cons / 100.0) * odds - 1.0
    return {'side': side, 'line': line, 'odds': odds,
            'p_raw': p_raw, 'p_cons': p_cons, 'stress_ok': p_cons_stress >= 100.0 / odds, 'ev': ev}

def score_candidate(c):
    # คะแนน 0-100 ต่อเส้น: EV 60% + ส่วนต่างความน่าจะเป็นเหนือเส้นคุ้มทุน 40% (ต่อเนื่อง ไม่มีหน้าผา)
    p_be = 100.0 / c['odds']
    s_edge = _clamp01(c['ev'] / 0.08) * 100               # EV +8% ขึ้นไป = เต็ม
    s_prob = _clamp01((c['p_cons'] - p_be) / 10.0) * 100  # ชนะเส้นคุ้มทุน 10 จุด = เต็ม
    return round(s_edge * 0.6 + s_prob * 0.4, 1)

def grade_of(score):
    return 'A' if score >= 70 else ('B' if score >= 55 else 'C')

def parse_market_prices(game_data):
    """
    ดึงราคา Under/Over จากทุกเส้นในฟีดราคา (ไม่ตรึงเส้น 2.5) — คืน list ของ {line, side, odds}
    รับเฉพาะเส้นครึ่ง (x.5) เพราะไม่มีผลเสมอ และข้ามมาร์เก็ตมุม/ใบเหลือง/ครึ่งเวลา
    """
    best_price = {}
    try:
        for market in game_data.get('odds', []):
            market_name = market.get('marketName', '').lower()
            if not ('under' in market_name or 'over' in market_name):
                continue
            if any(word in market_name for word in ['corner', 'card', 'half', 'shot', 'booking', 'race']):
                continue
            m = re.search(r'(\d+(?:\.\d+)?)', market_name)
            market_line = float(m.group(1)) if m else None
            for odd in market.get('odds', []):
                name = odd.get('name', '').lower()
                side = 'over' if name.startswith('over') else ('under' if name.startswith('under') else None)
                if side is None:
                    continue
                line = market_line
                if line is None:
                    # เส้นอาจระบุอยู่ในชื่อโพยแทน เช่น "Over 3.5"
                    m2 = re.search(r'(\d+(?:\.\d+)?)', name)
                    if not m2:
                        continue
                    line = float(m2.group(1))
                if line % 1 != 0.5 or line < 0.5:
                    continue
                try: val = float(odd.get('value') or 0)
                except (TypeError, ValueError): val = 0.0
                if val < 1.01:
                    continue
                key = (line, side)
                if key not in best_price or val > best_price[key]['odds']:
                    best_price[key] = {'line': line, 'side': side, 'odds': val}
    except Exception:
        pass
    return list(best_price.values())

def fair_line_suggestion(lambda_home, lambda_away, model_trust, stress_pct):
    """
    สำหรับคู่ที่ตลาดยังไม่เปิดราคา: แนะนำเส้นรอบๆ ผลรวม xG พร้อมราคาขั้นต่ำที่ควรรับ
    (คำนวณจากความน่าจะเป็นแบบปรับความเสี่ยงแล้ว เพื่อให้ราคาที่รอมี margin ความปลอดภัย)
    """
    lam = lambda_home + lambda_away
    stress = stress_pct / 100.0
    line_over = max(0.5, int(lam) - 0.5)   # เส้นต่ำกว่าค่าคาดหวัง → ฝั่ง Over คือธรรมชาติของเกมนี้
    line_under = int(lam) + 0.5            # เส้นสูงกว่าค่าคาดหวัง → ฝั่ง Under
    p_over = 50.0 + ((100.0 - under_probability(lambda_home, lambda_away, goals=int(line_over), stress=-stress)) - 50.0) * model_trust
    p_under = 50.0 + (under_probability(lambda_home, lambda_away, goals=int(line_under), stress=+stress) - 50.0) * model_trust
    return f"Over {line_over:g} ควรรับ ≥ {100.0 / p_over:.2f} · Under {line_under:g} ควรรับ ≥ {100.0 / p_under:.2f}"

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
    for _, row in df_edited.iterrows():
        if row['✏️ Odds'] >= 1.01:
            excel_data.append({'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                               'คำแนะนำ': row['🎯 คำแนะนำ'],
                               'ใส่ Odds ตรงนี้': row['✏️ Odds'],
                               'P ปลอดภัย (%)': row['P ปลอดภัย (%)']})
    if not excel_data: return None
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(excel_data).to_excel(writer, index=False, sheet_name='Bet Plan', startrow=1, header=False)
        workbook = writer.book; worksheet = writer.sheets['Bet Plan']
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#4F81BD', 'font_color': 'white', 'border': 1})
        for col_num, header in enumerate(['คู่บอล', 'คำแนะนำ', 'ใส่ Odds ตรงนี้', 'P ปลอดภัย (%)', '💰 เงินแทงอัตโนมัติ (บาท)']):
            worksheet.write(0, col_num, header, header_format)
        worksheet.set_column('A:A', 35); worksheet.set_column('B:B', 12); worksheet.set_column('C:C', 15); worksheet.set_column('D:D', 15); worksheet.set_column('E:E', 30)
        money_format = workbook.add_format({'num_format': '#,##0" ฿"', 'align': 'center'})
        for row_num in range(1, len(excel_data) + 1):
            formula = f'=IF(C{row_num}>=1.01, MIN(MAX((((C{row_num}-1)*(D{row_num}/100)-(1-(D{row_num}/100)))/(C{row_num}-1))*30, 0), 5) * {current_bankroll} / 100, 0)'
            worksheet.write_formula(row_num, 4, formula)
            worksheet.set_format(row_num, 4, money_format)
    output.seek(0)
    return output

# ==========================================
# ✅ เตรียมหน่วยความจำ (Session State) ให้พร้อม
# ==========================================
for key in ['scan_results', 'near_misses', 'watchlist']:
    if key not in st.session_state:
        st.session_state[key] = []

# ==========================================
# UI หลัก
# ==========================================
st.markdown("""
<div class="hero">
    <div class="hero-title">⚽ UNDER / OVER BOT<span class="ver">v4.1 Multi-Line</span></div>
    <div class="hero-sub">สแกนทุกเส้น Under/Over ที่ตลาดเปิดราคา แล้วเลือกเส้นที่คุ้มที่สุดของแต่ละคู่ — คัดเฉพาะคู่ที่มีราคาตลาดจริงยืนยัน</div>
</div>
""", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["🔍 สแกน & บันทึกคู่วันนี้", "📊 ตรวจสอบผลรางวัลเมื่อวาน"])

# ==========================================
# TAB 1
# ==========================================
with tab1:
    st.markdown("##### 🎛️ เลือกฝั่งที่ต้องการสแกน")
    scan_mode = st.radio("โหมดสแกน", options=['under', 'over', 'both'], index=2,
                         format_func=lambda m: MODE_LABEL[m], horizontal=True, label_visibility='collapsed')
    sides_to_scan = ['under'] if scan_mode == 'under' else ['over'] if scan_mode == 'over' else ['under', 'over']

    with st.expander("⚙️ ตั้งค่าเกณฑ์การคัดกรอง", expanded=False):
        st.markdown("🛡️ **ระดับความเข้มงวดของโมเดล** (ยิ่งเข้มงวด คู่ยิ่งน้อยแต่แน่นอนขึ้น)")
        sc1, sc2 = st.columns(2)
        with sc1:
            model_trust = st.slider("ความเชื่อมั่นในโมเดล Poisson", min_value=0.50, max_value=1.00, value=0.85, step=0.05,
                                    help="1.00 = เชื่อค่าที่โมเดลคำนวณเต็มร้อย | ยิ่งต่ำ = บีบความน่าจะเป็นเข้าหา 50% มากขึ้น เพื่อกันกรณีโมเดลประเมินเกิน")
        with sc2:
            stress_pct = st.slider("Stress Test: สมมติ xG คลาดเคลื่อน (%)", min_value=0, max_value=20, value=10, step=5,
                                   help="ตรวจว่าถ้า xG คลาดเคลื่อนไปทางร้ายตาม % นี้ (ฝั่งที่เสียเปรียบการแทงของเรา) เส้นนั้นจะยังคุ้มทุนอยู่ไหม — ไม่ผ่านด่านนี้จะไม่ถูกคัดเลย")
        st.markdown("🎚️ **เกณฑ์การคัดคู่** (ใช้กับทุกเส้น)")
        gc1, gc2, gc3 = st.columns(3)
        with gc1:
            min_odds = st.slider("ราคาต่ำสุดที่ยอมรับ", min_value=1.10, max_value=2.00, value=1.30, step=0.05,
                                 help="กันราคาจ่ายต่ำเกินไป — ราคายิ่งต่ำ เส้นคุ้มทุนยิ่งสูง (ราคา 1.30 ต้องชนะ 76.9% ถึงจะคุ้ม) โมเดลพลาดแม้เล็กน้อยก็ขาดทุน")
        with gc2:
            min_ev = st.slider("EV ขั้นต่ำ (%)", min_value=0, max_value=15, value=2, step=1,
                               help="มูลค่าพรีเมียมเหนือเส้นคุ้มทุนที่ต้องการ — ราคาตลาดต้องดีกว่าที่โมเดลคิดอย่างน้อยเท่านี้จึงจะแนะนำ")
        with gc3:
            score_min = st.slider("คะแนนขั้นต่ำ", min_value=20, max_value=90, value=50, step=5,
                                  help="คะแนน = EV เทียบราคาจริง 60% + ส่วนต่างความน่าจะเป็นเหนือเส้นคุ้มทุน 40% (คำนวณต่อเส้นที่เลือก)")
        bankroll = st.number_input("💰 เงินทุนทั้งหมด (บาท)", min_value=100, value=5000, step=100)

    # ❌ ขั้นตอนที่ 1: ถ้ากดปุ่ม ให้ "ทำงานหนัก" แล้วเก็บผลลัพธ์เข้า Session State
    if st.button("🔍 เริ่มค้นหาคู่เกมวันนี้", type="primary", use_container_width=True):
        today_str = datetime.now().strftime('%Y-%m-%d')
        LIST_URL = f"https://api.sstats.net/games/list?date={today_str}"
        STATS_URL_FORMAT = "https://api.sstats.net/games/glicko/{}"

        with st.spinner('กำลังดึงรายการแมตช์...'):
            games = []; api_error = ''; raw_count = 0; filtered_out = 0
            try:
                res = requests.get(LIST_URL, headers=HEADERS, timeout=30)
                if res.status_code != 200:
                    api_error = f"HTTP {res.status_code} — {res.text[:200]}"
                else:
                    data = res.json().get('data')
                    if not isinstance(data, list):
                        api_error = f"การตอบกลับผิดรูปแบบ: {str(data)[:200]}"
                    else:
                        raw_count = len(data)
                        games = [g for g in data if g.get('statusName', '').lower() not in ['finished', 'cancelled', 'postponed']]
                        filtered_out = raw_count - len(games)
            except requests.exceptions.RequestException as e:
                api_error = f"เชื่อมต่อไม่สำเร็จ: {type(e).__name__}: {e}"
            except ValueError as e:
                api_error = f"API ตอบกลับไม่ใช่ JSON: {e}"

        temp_approved = []; temp_near = []; temp_watch = []

        if not games:
            st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้")
            if api_error:
                st.error(f"**สาเหตุที่ตรวจพบ (ฝั่ง API):** {api_error}")
                st.caption("401 = คีย์ไม่ถูกต้อง/หมดอายุ · 402/403 = โควต้าหมดหรือสิทธิ์แพ็กเกจไม่ครอบคลุม · 429 = เรียกถี่เกินขีดจำกัด · 5xx = เซิร์ฟเวอร์ฝั่ง API มีปัญหา")
            elif raw_count > 0:
                st.info(f"API ส่งรายการมา {raw_count} คู่ แต่ทั้งหมดมีสถานะจบแล้ว/ยกเลิก/เลื่อน ({filtered_out} คู่) — ลองสแกนช่วงก่อนเกมเตะ หรือตรวจว่าวันที่ที่ถาม API ตรงกับวันแข่งจริงไหม")
            else:
                st.info("API ตอบกลับปกติแต่รายการว่างเปล่า — อาจเป็นช่วงที่ API ยังไม่อัปเดตโปรแกรมของวันนี้ ลองใหม่ภายหลัง")
        else:
            st.info(f"พบ {len(games)} คู่ · โหมด: {MODE_LABEL[scan_mode]} — กำลังวิเคราะห์...")
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
                    league_display = f"{country} - {league}" if country else league

                    # ราคาจริงทุกเส้นจากฟีด — กรองตามโหมดและราคาต่ำสุดที่ยอมรับ
                    prices = [p for p in parse_market_prices(g)
                              if p['odds'] >= min_odds and p['side'] in sides_to_scan]
                    if prices:
                        cands = []
                        for p in prices:
                            c = evaluate_line(p['side'], p['line'], home_xg, away_xg, p['odds'], model_trust, stress_pct)
                            c['score'] = score_candidate(c)
                            cands.append(c)
                        # เลือกเส้นที่ให้มูลค่าสูงสุดของคู่นี้ (หนึ่งคู่ = หนึ่งคำแนะนำ กันการแทงซ้ำสัมพันธ์กัน)
                        best = max(cands, key=lambda c: c['ev'])
                        others = sorted(cands, key=lambda c: c['ev'], reverse=True)[1:3]
                        alt_text = " · ".join([f"{'O' if c['side'] == 'over' else 'U'}{c['line']:g} @{c['odds']:.2f} ({c['ev'] * 100:+.1f}%)" for c in others])

                        qualified = (best['ev'] * 100 >= min_ev) and best['stress_ok'] and (best['score'] >= score_min)
                        match_data = {'🎯 คำแนะนำ': f"{'Over' if best['side'] == 'over' else 'Under'} {best['line']:g}",
                                      '⏰ เวลา': format_match_time(raw_date), '🏆 ลีก': league_display,
                                      'ทีมเหย้า': home, 'ทีมเยือน': away, 'xG รวม': combined_xg,
                                      'Poisson (%)': round(best['p_raw'], 1),
                                      'P ปลอดภัย (%)': round(best['p_cons'], 1),
                                      '✏️ Odds': best['odds'],
                                      'EV (%)': round(best['ev'] * 100, 1), 'คะแนน': best['score'],
                                      'เกรด': grade_of(best['score']),
                                      'เส้นอื่นที่ใกล้เคียง': alt_text if alt_text else '—',
                                      'bet_type': best['side'], 'line': best['line'],
                                      'game_id': game_id, 'stake_amount': 0}

                        if qualified:
                            temp_approved.append(match_data)
                        elif best['score'] >= score_min - 10:
                            temp_near.append(match_data)
                    elif abs(combined_xg - 2.7) >= 0.6:
                        # ไม่มีราคาตลาดจริง — บันทึกเป็น watchlist พร้อมเส้น/ราคาที่ควรตาม ไม่ยัดเป็นคำแนะนำ
                        temp_watch.append({'⏰ เวลา': format_match_time(raw_date), '🏆 ลีก': league_display,
                                           'คู่บอล': f"{home} vs {away}", 'xG รวม': combined_xg,
                                           '💡 รอราคาที่ควรรับ': fair_line_suggestion(home_xg, away_xg, model_trust, stress_pct)})
                    time.sleep(0.5)
                except: time.sleep(1); continue

            progress_bar.empty(); progress_text.empty()

        # ✅ เก็บข้อมูลเข้า Memory แทนที่จะแสดงตรงนี้
        st.session_state.scan_results = temp_approved
        st.session_state.near_misses = temp_near
        st.session_state.watchlist = temp_watch

    # ✅ ขั้นตอนที่ 2: แสดงผลตาราง "ข้างนอก" ปุ่มกด (จะไม่หายแม้คุณจะพิมพ์แก้ไข)
    if st.session_state.scan_results:
        st.markdown(f'<div class="side-header both">🎯 คู่ที่ผ่านเกณฑ์ — พบ {len(st.session_state.scan_results)} คู่ (เลือกเส้นที่ EV ดีที่สุดของแต่ละคู่แล้ว)</div>', unsafe_allow_html=True)
        st.caption("แก้ราคาในคอลัมน์ ✏️ Odds ได้เลย — EV และเงินแทงจะคำนวณใหม่ทันที คู่ที่ EV กลายเป็นลบจะถูกตัดออกเอง")

        df = pd.DataFrame(st.session_state.scan_results)
        df = df.sort_values(by='คะแนน', ascending=False).reset_index(drop=True)

        edited_df = st.data_editor(df, disabled=["🎯 คำแนะนำ", "⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน", "xG รวม", "Poisson (%)", "P ปลอดภัย (%)", "EV (%)", "คะแนน", "เกรด", "เส้นอื่นที่ใกล้เคียง", "bet_type", "line", "game_id", "stake_amount"],
                                   width="stretch", height=400, hide_index=True)

        # คำนวณเงินแทงจากค่าที่ถูกแก้ไขแล้ว — ใช้ P ปลอดภัยใน Kelly: ถ้า EV ติดลบ Kelly จะเป็นลบและตัดคู่นั้นทิ้งเอง
        final_bets_all = []
        for _, row in edited_df.iterrows():
            odds = row['✏️ Odds']; prob = row['P ปลอดภัย (%)']
            if odds >= min_odds:
                stake_pct, bet_amount = calculate_kelly_stake(odds, prob, bankroll)
                if bet_amount > 0:
                    row = row.copy(); row['stake_amount'] = bet_amount
                    row['EV (%)'] = round(((prob / 100.0) * odds - 1.0) * 100, 1)
                    final_bets_all.append({**row.to_dict()})

        if final_bets_all:
            df_bets = pd.DataFrame(final_bets_all)[['🎯 คำแนะนำ', 'ทีมเหย้า', 'ทีมเยือน', '✏️ Odds', 'EV (%)', 'stake_amount']].rename(columns={'stake_amount': '💰 แทง (บาท)'})
            df_bets['💰 แทง (บาท)'] = df_bets['💰 แทง (บาท)'].apply(lambda x: f"{x:,.0f} ฿")
            df_bets['EV (%)'] = df_bets['EV (%)'].apply(lambda x: f"{x:+.1f}%")
            st.dataframe(df_bets, width="stretch", hide_index=True)

            st.divider()
            excel_file = create_excel_with_formula(edited_df, bankroll)
            if excel_file:
                st.download_button(label="📥 ดาวน์โหลดไฟล์ Excel", data=excel_file,
                                   file_name=f'UnderOver_Bet_{datetime.now().strftime("%Y-%m-%d")}.xlsx',
                                   mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

            if st.button("💾 ยืนยันการบันทึกคู่เหล่านี้เพื่อตรวจสอบผลวันพรุ่งนี้", type="secondary", use_container_width=True):
                save_bets_to_db(final_bets_all, datetime.now().strftime('%Y-%m-%d'))
                st.success("✅ บันทึกลงระบบสำเร็จแล้ว! พรุ่งนี้มากด Tab 2 เพื่อดูผลลัพธ์ได้เลย")
                st.session_state.scan_results = []; st.session_state.near_misses = []; st.session_state.watchlist = []
        else:
            st.warning("ไม่มีคู่ที่มีมูลค่าพอจะแทงตามราคาปัจจุบัน — ลองแก้ราคาในคอลัมน์ ✏️ Odds หรือปรับเกณฑ์การคัดกรอง")

    if st.session_state.near_misses:
        st.markdown('<div class="side-header both">⚠️ คู่ที่ใกล้เคียงเกณฑ์</div>', unsafe_allow_html=True)
        df_near = pd.DataFrame(st.session_state.near_misses)
        df_near = df_near.sort_values(by='คะแนน', ascending=False).head(3).reset_index(drop=True)
        st.dataframe(df_near, width="stretch", hide_index=True)

    if st.session_state.watchlist:
        with st.expander(f"👀 คู่ที่โมเดลเห็นสถานการณ์ไม่ธรรมดาแต่ยังไม่มีราคาตลาด ({len(st.session_state.watchlist)} คู่)", expanded=False):
            st.caption("รอจนกว่าเว็บจะเปิดราคาแล้วเทียบกับ 'ราคาที่ควรรับ' — ถ้าราคาจริงดีกว่าที่แสดงจึงค่อยพิจารณา (กดสแกนใหม่เพื่ออัปเดต)")
            df_watch = pd.DataFrame(st.session_state.watchlist)
            df_watch = df_watch.sort_values(by='xG รวม', key=lambda s: (s - 2.7).abs(), ascending=False).head(8).reset_index(drop=True)
            st.dataframe(df_watch, width="stretch", hide_index=True)


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
            stats = {bt: {'n': 0, 'wins': 0, 'pl': 0.0, 'staked': 0.0} for bt in ['under', 'over']}
            progress_text = st.empty(); progress_bar = st.progress(0)
            LIST_URL_YEST = f"https://api.sstats.net/games/list?date={yesterday_date}"

            try:
                res_list = requests.get(LIST_URL_YEST, headers=HEADERS, timeout=15).json()
                all_yesterday_games = {str(g.get('id')): g for g in res_list.get('data', []) if g.get('statusName', '').lower() == 'finished'}
            except:
                all_yesterday_games = {}

            for index, row in pending_df.iterrows():
                game_id = row['game_id']
                bet_type = row['bet_type'] if 'bet_type' in pending_df.columns and row['bet_type'] in ('under', 'over') else 'under'
                bet_line = 2.5
                if 'line' in pending_df.columns and pd.notna(row['line']):
                    try: bet_line = float(row['line'])
                    except (TypeError, ValueError): bet_line = 2.5
                progress_text.text(f"ตรวจสอบ: {row['home']} vs {row['away']}")
                progress_bar.progress((index + 1) / len(pending_df))

                if game_id in all_yesterday_games:
                    g = all_yesterday_games[game_id]
                    home_ft = g.get('homeFTResult', 0) or 0; away_ft = g.get('awayFTResult', 0) or 0
                    total_goals = int(home_ft) + int(away_ft)
                    # เส้นครึ่งไม่มีผลเสมอ: เช่นเส้น 3.5 → Under ชนะเมื่อยิงรวม ≤ 3, Over ชนะเมื่อยิงรวม ≥ 4
                    goal_limit = int(bet_line)
                    is_win = (total_goals <= goal_limit) if bet_type == 'under' else (total_goals >= goal_limit + 1)
                    profit_loss = (row['stake'] * (row['odds'] - 1)) if is_win else -row['stake']

                    status_str = '✅ ได้' if is_win else '❌ เสีย'
                    update_bet_result(row['id'], 'won' if is_win else 'lost', profit_loss)
                    s = stats[bet_type]
                    s['n'] += 1; s['wins'] += int(is_win); s['pl'] += profit_loss; s['staked'] += row['stake']

                    results.append({
                        '🎯 ประเภท': f"{'🔽' if bet_type == 'under' else '🔼'} {'Under' if bet_type == 'under' else 'Over'} {bet_line:g}",
                        '🏆 ลีก': row['league'],
                        'คู่บอล': f"{row['home']} vs {row['away']}",
                        'สกอร์จริง': f"{home_ft}-{away_ft} (รวม {total_goals})",
                        '💰 เดิมพัน': f"{row['stake']:.0f} ฿",
                        'ผลลัพธ์': status_str,
                        'กำไร/ขาดทุน': f"{'+' if profit_loss > 0 else ''}{profit_loss:.0f} ฿"
                    })
                else:
                    results.append({
                        '🎯 ประเภท': f"{'🔽' if bet_type == 'under' else '🔼'} {'Under' if bet_type == 'under' else 'Over'} {bet_line:g}",
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

                played = [r for r in results if 'ไม่แข่ง' not in r['ผลลัพธ์']]
                if played:
                    total_n = sum(stats[bt]['n'] for bt in ['under', 'over'])
                    total_wins = sum(stats[bt]['wins'] for bt in ['under', 'over'])
                    total_pl = sum(stats[bt]['pl'] for bt in ['under', 'over'])
                    total_staked = sum(stats[bt]['staked'] for bt in ['under', 'over'])
                    win_rate = (total_wins / total_n) * 100 if total_n else 0
                    roi = (total_pl / total_staked) * 100 if total_staked > 0 else 0

                    c1, c2, c3 = st.columns(3)
                    c1.metric("🎯 คู่ที่แทงทั้งหมด", total_n)
                    c2.metric("Win Rate รวม", f"{win_rate:.1f}%")
                    c3.metric("ROI รวม", f"{roi:.2f}%", delta=f"{total_pl:+.0f} ฿")

                    # สถิติแยกฝั่ง — ใช้ตัดสินใจว่าจะเล่นฝั่งไหนต่อ
                    side_cols = st.columns(2)
                    for i, bt in enumerate(['under', 'over']):
                        s = stats[bt]
                        if s['n']:
                            wr = (s['wins'] / s['n']) * 100
                            roi_s = (s['pl'] / s['staked']) * 100 if s['staked'] > 0 else 0
                            with side_cols[i]:
                                st.metric(f"{SIDE_LABEL[bt]} · WR {wr:.0f}%", f"{s['n']} คู่", f"{s['pl']:+.0f} ฿ (ROI {roi_s:+.1f}%)")

                st.dataframe(df_results, width="stretch", hide_index=True)
                st.success("✅ อัปเดตผลแทงลงในระบบแล้ว คู่เหล่านี้จะไม่ถูกนำมาคำนวณซ้ำ")
