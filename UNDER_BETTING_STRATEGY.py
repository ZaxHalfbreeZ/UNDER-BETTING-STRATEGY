"""
⚽ UNDER / OVER BOT v4.0 — Smart xG + Poisson Scanner
รันด้วย: streamlit run under_over_bot_v4.py
ต้องมีไฟล์ .streamlit/secrets.toml ที่มี API_KEY = "..." (เหมือน v3)
Dependencies: streamlit, requests, pandas, scipy, xlsxwriter
"""
import streamlit as st
import requests
import pandas as pd
import time
import io
import sqlite3
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

st.set_page_config(page_title="Under/Over Bot v4.0", page_icon="⚽", layout="wide")

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
                  bet_type TEXT DEFAULT 'under',
                  ev REAL DEFAULT 0, prob_cons REAL DEFAULT 0,
                  status TEXT DEFAULT 'pending', profit REAL DEFAULT 0)''')
    conn.commit()
    conn.close()

def migrate_db():
    # ไฟล์ .db จาก v3 ยังไม่มีคอลัมน์ bet_type — CREATE TABLE IF NOT EXISTS ไม่เติมให้ ต้อง ALTER เอง
    conn = sqlite3.connect('betting_log.db')
    c = conn.cursor()
    # คอลัมน์ที่เพิ่มใน v4: bet_type (under/over), ev, prob_cons (ความน่าจะเป็นแบบปรับความเสี่ยงแล้ว)
    for stmt in ("ALTER TABLE pending_bets ADD COLUMN bet_type TEXT DEFAULT 'under'",
                 "ALTER TABLE pending_bets ADD COLUMN ev REAL DEFAULT 0",
                 "ALTER TABLE pending_bets ADD COLUMN prob_cons REAL DEFAULT 0"):
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
                     (scan_date, game_id, home, away, league, xg, poisson, score, odds, stake, bet_type, ev, prob_cons)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (scan_date, b['game_id'], b['ทีมเหย้า'], b['ทีมเยือน'], b['🏆 ลีก'],
                   b['xG รวม'], b['Poisson (%)'], b['คะแนน'], b['✏️ Odds 2.5'], b['stake_amount'], b['bet_type'],
                   b['EV (%)'], b['P ปลอดภัย (%)']))
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

def under_probability(lambda_home, lambda_away, stress=0.0):
    # ผลรวมของ Poisson สองตัว = Poisson(ผลรวม xG): เส้น 2.5 คือ P(ยิงรวมกันไม่เกิน 2 ประตู)
    total = (lambda_home + lambda_away) * (1.0 + stress)
    return float(poisson.cdf(2, total) * 100)

def evaluate_match(side, lambda_home, lambda_away, odds, xg_target, model_trust, stress_pct):
    """
    ให้คะแนนแบบอนุรักษ์นิยมเพื่อความแน่นอน (คืน dict):
      p_raw    : ความน่าจะเป็นดิบจาก Poisson
      p_cons   : ค่าที่ใช้ตัดสินจริง — บีบเข้าหา 50% ตาม model_trust (กันโมเดลพลาด)
      ev       : Expected Value เทียบราคาที่แทงจริง (ถ้าตลาดยังไม่เปิดราคา ใช้ benchmark 1.90)
      score    : 0-100 = EV 45% + ส่วนต่างความน่าจะเป็นเหนือเส้นคุ้มทุน 35% + ส่วนต่าง xG 20%
      qualified: ต้องผ่านครบทุกด่าน (prob/ev/xg/stress) ไม่ใช่แค่คะแนนรวมถึงเกณฑ์
    """
    stress = stress_pct / 100.0
    if side == 'under':
        p_raw = under_probability(lambda_home, lambda_away)
        p_stress = under_probability(lambda_home, lambda_away, stress=+stress)          # ถ้าบอลไหลมากกว่า xG
    else:
        p_raw = 100.0 - under_probability(lambda_home, lambda_away)
        p_stress = 100.0 - under_probability(lambda_home, lambda_away, stress=-stress)  # ถ้าบอลน้อยกว่า xG

    p_cons = 50.0 + (p_raw - 50.0) * model_trust
    p_cons_stress = 50.0 + (p_stress - 50.0) * model_trust

    has_real_odds = odds >= 1.50
    book_odds = odds if has_real_odds else 1.90
    p_be = 100.0 / book_odds                    # เส้นคุ้มทุนของราคาที่แทงจริง
    ev = (p_cons / 100.0) * book_odds - 1.0     # มูลค่าที่คาดหวังต่อเงิน 1 หน่วย

    combined_xg = lambda_home + lambda_away
    if xg_target > 0:
        xg_margin = (xg_target - combined_xg) / xg_target if side == 'under' else (combined_xg - xg_target) / xg_target
    else:
        xg_margin = 0.0

    s_edge = _clamp01(ev / 0.10) * 100                 # EV +10% ขึ้นไป = เต็ม
    s_prob = _clamp01((p_cons - p_be) / 12.0) * 100    # ชนะเส้นคุ้มทุน 12 จุด = เต็ม
    s_xg = _clamp01(xg_margin / 0.25) * 100            # ห่างจากเป้า xG 25% = เต็ม
    score = round(s_edge * 0.45 + s_prob * 0.35 + s_xg * 0.20, 1)

    gates = {'prob': p_cons >= p_be,           # หลังหักความเชื่อมั่นแล้วยังชนะราคา
             'ev': ev > 0,                     # มีมูลค่าเชิงราคา
             'xg': xg_margin > 0,              # xG อยู่ฝั่งเดียวกับที่แทง (confluence กับ Poisson)
             'stress': p_cons_stress >= p_be}  # แม้ xG เพี้ยนทางร้ายตาม stress_pct ก็ยังคุ้มทุน
    grade = 'A' if score >= 75 else ('B' if score >= 65 else 'C')
    return {'p_raw': p_raw, 'p_cons': p_cons, 'ev': ev, 'score': score, 'grade': grade,
            'qualified': all(gates.values()), 'has_real_odds': has_real_odds}

def _extract_side_odds(game_data, side):
    try:
        for market in game_data.get('odds', []):
            market_name = market.get('marketName', '').lower()
            if '2.5' in market_name or 'under/over' in market_name:
                for odd in market.get('odds', []):
                    name = odd.get('name', '').lower()
                    # ฝั่ง over ต้องใช้ startswith เพราะชื่อมาร์เก็ต "Under/Over" มีคำว่า over ซ่อนอยู่
                    hit = name.startswith('over') if side == 'over' else 'under' in name
                    if hit:
                        try: val = float(odd.get('value') or 0)
                        except (TypeError, ValueError): val = 0.0
                        if val >= 1.50: return val
        return 0.0
    except: return 0.0

def get_under_25_odds(game_data):
    return _extract_side_odds(game_data, 'under')

def get_over_25_odds(game_data):
    return _extract_side_odds(game_data, 'over')

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

def create_excel_with_formula(sides, current_bankroll):
    # sides = list ของ (bet_type, edited_df) → หนึ่ง sheet ต่อฝั่ง
    output = io.BytesIO()
    try: import xlsxwriter
    except ImportError: return None
    wrote_any = False
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for bet_type, df_side in sides:
            excel_data = []
            for _, row in df_side.iterrows():
                if row['✏️ Odds 2.5'] >= 1.50:
                    excel_data.append({'คู่บอล': f"{row['ทีมเหย้า']} vs {row['ทีมเยือน']}",
                                       'ใส่ Odds ตรงนี้': row['✏️ Odds 2.5'],
                                       'P ปลอดภัย (%)': row['P ปลอดภัย (%)']})
            if not excel_data: continue
            sheet_name = 'Under 2.5' if bet_type == 'under' else 'Over 2.5'
            pd.DataFrame(excel_data).to_excel(writer, index=False, sheet_name=sheet_name, startrow=1, header=False)
            workbook = writer.book; worksheet = writer.sheets[sheet_name]
            header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#4F81BD', 'font_color': 'white', 'border': 1})
            for col_num, header in enumerate(['คู่บอล', 'ใส่ Odds ตรงนี้', 'P ปลอดภัย (%)', '💰 เงินแทงอัตโนมัติ (บาท)']):
                worksheet.write(0, col_num, header, header_format)
            worksheet.set_column('A:A', 35); worksheet.set_column('B:B', 25); worksheet.set_column('C:C', 20); worksheet.set_column('D:D', 30)
            money_format = workbook.add_format({'num_format': '#,##0" ฿"', 'align': 'center'})
            for row_num in range(1, len(excel_data) + 1):
                formula = f'=IF(B{row_num}>=1.5, MIN(MAX((((B{row_num}-1)*(C{row_num}/100)-(1-(C{row_num}/100)))/(B{row_num}-1))*30, 0), 5) * {current_bankroll} / 100, 0)'
                worksheet.write_formula(row_num, 3, formula)
                worksheet.set_format(row_num, 3, money_format)
            wrote_any = True
    if not wrote_any: return None
    output.seek(0)
    return output

# ==========================================
# ✅ เตรียมหน่วยความจำ (Session State) ให้พร้อม
# ==========================================
for key in ['scan_results_under', 'scan_results_over', 'near_misses_under', 'near_misses_over']:
    if key not in st.session_state:
        st.session_state[key] = []

# ==========================================
# UI หลัก
# ==========================================
st.markdown("""
<div class="hero">
    <div class="hero-title">⚽ UNDER / OVER BOT<span class="ver">v4.0</span></div>
    <div class="hero-sub">Smart xG + Poisson Scanner — สแกนคู่บอลได้ทั้งฝั่ง Under และ Over 2.5 ในการสแกนเดียว</div>
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
        st.markdown("🛡️ **ระดับความเข้มงวดของโมเดล** (ใช้ร่วมกันทุกฝั่ง — ยิ่งเข้มงวด คู่ยิ่งน้อยแต่แน่นอนขึ้น)")
        sc1, sc2 = st.columns(2)
        with sc1:
            model_trust = st.slider("ความเชื่อมั่นในโมเดล Poisson", min_value=0.50, max_value=1.00, value=0.85, step=0.05,
                                    help="1.00 = เชื่อค่าที่โมเดลคำนวณเต็มร้อย | ยิ่งต่ำ = บีบความน่าจะเป็นเข้าหา 50% มากขึ้น เพื่อกันกรณีโมเดลประเมินเกิน")
        with sc2:
            stress_pct = st.slider("Stress Test: สมมติ xG คลาดเคลื่อน (%)", min_value=0, max_value=20, value=10, step=5,
                                   help="ตรวจว่าถ้า xG คลาดเคลื่อนไปทางร้ายตาม % นี้ (ฝั่งที่เสียเปรียบการแทงของเรา) คู่นั้นจะยังคุ้มทุนอยู่ไหม — ไม่ผ่านด่านนี้จะไม่ถูกคัดเลย")
        score_targets = {}; xg_targets = {}
        col1, col2 = st.columns(2)
        if 'under' in sides_to_scan:
            with col1:
                st.markdown("🔽 **ฝั่ง Under 2.5**")
                score_targets['under'] = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ (Under)", min_value=40, max_value=95, value=60, step=5,
                                                   help="คะแนนเชิงมูลค่า: EV เทียบราคาจริง 45% + ความน่าจะเป็นเหนือเส้นคุ้มทุน 35% + ส่วนต่าง xG 20%")
                xg_targets['under'] = st.slider("📊 xG รวมสูงสุดที่ยอมรับ (Under)", min_value=2.0, max_value=3.5, value=2.6, step=0.1)
        if 'over' in sides_to_scan:
            with col2:
                st.markdown("🔼 **ฝั่ง Over 2.5**")
                score_targets['over'] = st.slider("🎯 คะแนนผ่านเกณฑ์ขั้นต่ำ (Over)", min_value=40, max_value=95, value=60, step=5,
                                                  help="คะแนนเชิงมูลค่า: EV เทียบราคาจริง 45% + ความน่าจะเป็นเหนือเส้นคุ้มทุน 35% + ส่วนต่าง xG 20%")
                xg_targets['over'] = st.slider("📊 xG รวมขั้นต่ำที่ยอมรับ (Over)", min_value=2.6, max_value=4.0, value=3.2, step=0.1)
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

        temp_approved = {'under': [], 'over': []}; temp_near = {'under': [], 'over': []}

        if not games:
            st.warning("ไม่พบแมตช์ที่กำลังจะแข่งในวันนี้")
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

                    for bet_type in sides_to_scan:
                        odds = get_under_25_odds(g) if bet_type == 'under' else get_over_25_odds(g)
                        eva = evaluate_match(bet_type, home_xg, away_xg, odds,
                                             xg_targets.get(bet_type, 2.6 if bet_type == 'under' else 3.2),
                                             model_trust, stress_pct)

                        match_data = {'🎯 ประเภท': SIDE_LABEL[bet_type], '⏰ เวลา': format_match_time(raw_date),
                                      '🏆 ลีก': league_display, 'ทีมเหย้า': home, 'ทีมเยือน': away,
                                      'xG รวม': combined_xg,
                                      'Poisson (%)': round(eva['p_raw'], 1),
                                      'P ปลอดภัย (%)': round(eva['p_cons'], 1),
                                      'EV (%)': round(eva['ev'] * 100, 1), 'คะแนน': eva['score'],
                                      'เกรด': (eva['grade'] + '⚠️') if eva['qualified'] else '—',
                                      '✏️ Odds 2.5': odds, 'game_id': game_id, 'stake_amount': 0}

                        if eva['qualified'] and eva['score'] >= score_targets.get(bet_type, 60):
                            temp_approved[bet_type].append(match_data)
                        elif eva['score'] >= score_targets.get(bet_type, 60) - 10:
                            temp_near[bet_type].append(match_data)
                    time.sleep(0.5)
                except: time.sleep(1); continue

            progress_bar.empty(); progress_text.empty()

        # ✅ เก็บข้อมูลเข้า Memory แทนที่จะแสดงตรงนี้
        st.session_state.scan_results_under = temp_approved['under']
        st.session_state.scan_results_over = temp_approved['over']
        st.session_state.near_misses_under = temp_near['under']
        st.session_state.near_misses_over = temp_near['over']

    # ✅ ขั้นตอนที่ 2: แสดงผลตาราง "ข้างนอก" ปุ่มกด (จะไม่หายแม้คุณจะพิมพ์แก้ไข)
    final_bets_all = []
    excel_sides = []
    for bet_type in ['under', 'over']:
        if not st.session_state[f'scan_results_{bet_type}']: continue

        st.markdown(f'<div class="side-header {bet_type}">{SIDE_LABEL[bet_type]} — พบ {len(st.session_state[f"scan_results_{bet_type}"])} คู่ผ่านเกณฑ์ ✅</div>', unsafe_allow_html=True)
        st.caption("แก้ราคาในคอลัมน์ ✏️ Odds 2.5 ได้เลย — EV และเงินแทงจะคำนวณใหม่ทันที · เกรดต่อท้าย ⚠️ = คำนวณจากราคา benchmark 1.90 เพราะตลาดยังไม่เปิดราคาตอนสแกน")

        df = pd.DataFrame(st.session_state[f'scan_results_{bet_type}'])
        df = df.sort_values(by='คะแนน', ascending=False).reset_index(drop=True)

        edited_df = st.data_editor(df, disabled=["🎯 ประเภท", "⏰ เวลา", "🏆 ลีก", "ทีมเหย้า", "ทีมเยือน", "xG รวม", "Poisson (%)", "P ปลอดภัย (%)", "EV (%)", "คะแนน", "เกรด", "game_id", "stake_amount"],
                                   width="stretch", height=400, hide_index=True)

        # คำนวณเงินแทงจากค่าที่ถูกแก้ไขแล้ว — ใช้ P ปลอดภัยใน Kelly: ถ้า EV ติดลบ Kelly จะเป็นลบและตัดคู่นั้นทิ้งเอง
        final_bets = []
        for _, row in edited_df.iterrows():
            odds = row['✏️ Odds 2.5']; prob = row['P ปลอดภัย (%)']
            if odds >= 1.50:
                stake_pct, bet_amount = calculate_kelly_stake(odds, prob, bankroll)
                if bet_amount > 0:
                    row = row.copy(); row['stake_amount'] = bet_amount
                    row['EV (%)'] = round(((prob / 100.0) * odds - 1.0) * 100, 1)
                    final_bets.append({**row.to_dict(), 'bet_type': bet_type})

        if final_bets:
            df_bets = pd.DataFrame(final_bets)[['🎯 ประเภท', 'ทีมเหย้า', 'ทีมเยือน', '✏️ Odds 2.5', 'EV (%)', 'stake_amount']].rename(columns={'stake_amount': '💰 แทง (บาท)'})
            df_bets['💰 แทง (บาท)'] = df_bets['💰 แทง (บาท)'].apply(lambda x: f"{x:,.0f} ฿")
            df_bets['EV (%)'] = df_bets['EV (%)'].apply(lambda x: f"{x:+.1f}%")
            st.dataframe(df_bets, width="stretch", hide_index=True)
            final_bets_all.extend(final_bets)
            excel_sides.append((bet_type, edited_df))
        else:
            st.warning(f"ฝั่ง {SIDE_LABEL[bet_type]}: กรุณาใส่เลข Odds ที่มากกว่า 1.50 เพื่อคำนวณเงินแทง")

    if final_bets_all:
        st.divider()
        excel_file = create_excel_with_formula(excel_sides, bankroll)
        if excel_file:
            st.download_button(label="📥 ดาวน์โหลดไฟล์ Excel", data=excel_file,
                               file_name=f'UnderOver_Bet_{datetime.now().strftime("%Y-%m-%d")}.xlsx',
                               mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        if st.button("💾 ยืนยันการบันทึกคู่เหล่านี้เพื่อตรวจสอบผลวันพรุ่งนี้", type="secondary", use_container_width=True):
            save_bets_to_db(final_bets_all, datetime.now().strftime('%Y-%m-%d'))
            st.success("✅ บันทึกลงระบบสำเร็จแล้ว! พรุ่งนี้มากด Tab 2 เพื่อดูผลลัพธ์ได้เลย")
            st.session_state.scan_results_under = []; st.session_state.scan_results_over = []
            st.session_state.near_misses_under = []; st.session_state.near_misses_over = []

    for bet_type in ['under', 'over']:
        if st.session_state[f'near_misses_{bet_type}']:
            st.markdown(f'<div class="side-header {bet_type}">⚠️ {SIDE_LABEL[bet_type]} — คู่ที่ใกล้เคียงเกณฑ์</div>', unsafe_allow_html=True)
            df_near = pd.DataFrame(st.session_state[f'near_misses_{bet_type}'])
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
                progress_text.text(f"ตรวจสอบ: {row['home']} vs {row['away']}")
                progress_bar.progress((index + 1) / len(pending_df))

                if game_id in all_yesterday_games:
                    g = all_yesterday_games[game_id]
                    home_ft = g.get('homeFTResult', 0) or 0; away_ft = g.get('awayFTResult', 0) or 0
                    total_goals = int(home_ft) + int(away_ft)
                    # เส้น 2.5 ไม่มีผลเสมอ: Under ชนะเมื่อรวมสกอร์ไม่เกิน 2, Over ชนะเมื่อรวมสกอร์ตั้งแต่ 3 ขึ้นไป
                    is_win = (total_goals <= 2) if bet_type == 'under' else (total_goals >= 3)
                    profit_loss = (row['stake'] * (row['odds'] - 1)) if is_win else -row['stake']

                    status_str = '✅ ได้' if is_win else '❌ เสีย'
                    update_bet_result(row['id'], 'won' if is_win else 'lost', profit_loss)
                    s = stats[bet_type]
                    s['n'] += 1; s['wins'] += int(is_win); s['pl'] += profit_loss; s['staked'] += row['stake']

                    results.append({
                        '🎯 ประเภท': SIDE_LABEL[bet_type],
                        '🏆 ลีก': row['league'],
                        'คู่บอล': f"{row['home']} vs {row['away']}",
                        'สกอร์จริง': f"{home_ft}-{away_ft} (รวม {total_goals})",
                        '💰 เดิมพัน': f"{row['stake']:.0f} ฿",
                        'ผลลัพธ์': status_str,
                        'กำไร/ขาดทุน': f"{'+' if profit_loss > 0 else ''}{profit_loss:.0f} ฿"
                    })
                else:
                    results.append({
                        '🎯 ประเภท': SIDE_LABEL[bet_type],
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
