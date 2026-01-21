import yfinance as yf
import pandas as pd
import pandas_ta as ta
import smtplib
import os
import datetime
import sys
from email.message import EmailMessage
import requests
from io import StringIO 
from collections import Counter

# --- KONFIGURACJA ---
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465

SOURCES = {
    'S&P 500 (Large Cap)': 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
    'S&P 600 (Small Cap)': 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies'
}

def get_tickers_metadata(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        df = pd.read_html(StringIO(response.text), flavor='lxml')[0]
        df.rename(columns={'Security': 'Name', 'Company': 'Name', 'GICS Sector': 'Sector'}, inplace=True)
        df['Symbol'] = df['Symbol'].str.replace('.', '-')
        return df.set_index('Symbol')[['Name', 'Sector']].to_dict('index')
    except Exception as e:
        print(f"Błąd metadanych: {e}")
        return {}

def analyze_market(metadata, lookback_window=5):
    tickers = list(metadata.keys())
    if not tickers: return [], []
    # Pobieranie danych z yfinance
    data = yf.download(tickers, period="7mo", group_by='ticker', auto_adjust=True, progress=False)
    bullish, bearish = [], []
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue
            df = data[ticker].dropna().copy()
            if len(df) < 60: continue
            
            # Obliczenia techniczne: MA20, MA50, RSI, ADX
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['VolMA20'] = df['Volume'].rolling(window=20).mean()
            df['RSI'] = ta.rsi(df['Close'], length=14)
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            
            found_type, sessions_ago = None, 0
            for i in range(1, lookback_window + 1):
                t_idx, y_idx = -i, -(i + 1)
                if abs(y_idx) > len(df): break
                t_r, y_r = df.iloc[t_idx], df.iloc[y_idx]
                
                # Detekcja Golden i Death Cross
                if y_r['MA20'] <= y_r['MA50'] and t_r['MA20'] > t_r['MA50']:
                    found_type, sessions_ago = 'bullish', i - 1
                    break
                elif y_r['MA20'] >= y_r['MA50'] and t_r['MA20'] < t_r['MA50']:
                    found_type, sessions_ago = 'bearish', i - 1
                    break

            if found_type:
                today = df.iloc[-1]
                info = {
                    'ticker': ticker, 'name': metadata[ticker].get('Name', 'N/A'),
                    'sector': metadata[ticker].get('Sector', 'N/A'), 'close': today['Close'],
                    'ma20': today['MA20'], 'ma50': today['MA50'],
                    'dist_ma20': ((today['Close'] - today['MA20']) / today['MA20']) * 100,
                    'rsi': today['RSI'], 'adx': adx_df.iloc[-1]['ADX_14'] if adx_df is not None else 0,
                    'vol_ratio': today['Volume'] / today['VolMA20'] if today['VolMA20'] > 0 else 0,
                    'age': sessions_ago
                }
                bullish.append(info) if found_type == 'bullish' else bearish.append(info)
        except: continue
    return sorted(bullish, key=lambda x: x['age']), sorted(bearish, key=lambda x: x['age'])

def create_sector_summary(bullish, bearish):
    b_sectors = Counter([s['sector'] for s in bullish])
    d_sectors = Counter([s['sector'] for s in bearish])
    all_s = sorted(set(list(b_sectors.keys()) + list(d_sectors.keys())))
    if not all_s: return ""
    rows = "".join([f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee;'>{s}</td>"
                    f"<td style='text-align:center;color:green;'><b>{b_sectors.get(s,'-')}</b></td>"
                    f"<td style='text-align:center;color:red;'><b>{d_sectors.get(s,'-')}</b></td></tr>" for s in all_s])
    return f"<div style='margin:10px 0;padding:12px;background:#fcfcfc;border:1px solid #eee;display:inline-block;'>" \
           f"<h4 style='margin:0 0 8px 0;font-size:14px;'>Podsumowanie Sektorów:</h4>" \
           f"<table style='font-size:13px;border-collapse:collapse;'><tr style='background:#f0f0f0;'>" \
           f"<th>Sektor</th><th>Golden</th><th>Death</th></tr>{rows}</table></div>"

def create_table_html(signals, signal_type):
    if not signals: return "<p style='color:gray;font-size:14px;'>Brak sygnałów.</p>"
    
    age_colors = {0: "#007bff", 1: "#28a745", 2: "#ffc107", 3: "#fd7e14", 4: "#6f42c1"}
    rows = ""
    for s in signals:
        # Style Wieku
        age_bg = age_colors.get(s['age'], "#7f8c8d")
        age_text = "Dzisiaj" if s['age'] == 0 else f"{s['age']}d"
        age_style = f"background:{age_bg}; color:white; font-weight:bold; padding:2px 6px; border-radius:3px;"

        # --- LOGIKA ADX (15-25 pomarańczowy, >25 zielony) ---
        if s['adx'] > 25:
            adx_style = "color: #27ae60; font-weight: bold;"
        elif 15 <= s['adx'] <= 25:
            adx_style = "color: #e67e22; font-weight: bold;"
        else:
            adx_style = "color: #444;"

        # --- LOGIKA VOL/AVG (1-1.5 pomarańczowy, >1.5 zielony) ---
        if s['vol_ratio'] > 1.5:
            vol_style = "color: #27ae60; font-weight: bold;"
        elif 1.0 <= s['vol_ratio'] <= 1.5:
            vol_style = "color: #e67e22; font-weight: bold;"
        else:
            vol_style = "color: #444;"

        # Style RSI i Dystansu
        rsi_style = "color:#e67e22;font-weight:bold;" if s['rsi']>70 or s['rsi']<30 else ""
        if signal_type == 'bullish':
            dist_color = "#27ae60" if s['dist_ma20'] > 0 else "#e74c3c"
        else:
            dist_color = "#e74c3c" if s['dist_ma20'] > 0 else "#27ae60"
        dist_style = f"color: {dist_color}; font-weight: bold;"

        rows += f"""<tr style="border-bottom:1px solid #eee;font-size:14px;">
            <td style="padding:10px;"><b>{s['ticker']}</b></td><td style="font-size:13px;">{s['name']}</td>
            <td style="font-size:12px;color:#666;">{s['sector']}</td>
            <td style="text-align:center;"><span style="{age_style}">{age_text}</span></td>
            <td><b>{s['close']:.2f}</b></td><td style="color:#444;">{s['ma20']:.1f}/{s['ma50']:.1f}</td>
            <td style="{dist_style}">{s['dist_ma20']:+.1f}%</td><td style="{rsi_style}">{s['rsi']:.1f}</td>
            <td style="{adx_style}">{s['adx']:.1f}</td><td style="{vol_style}">{s['vol_ratio']:.2f}x</td></tr>"""
    
    return f"""<table style="width:100%;border-collapse:collapse;margin-bottom:25px;">
        <tr style="background:#f8f9fa;text-align:left;border-bottom:2px solid #dee2e6;font-size:13px;">
        <th style="padding:10px;">Ticker</th><th>Nazwa</th><th>Sektor</th><th style="text-align:center;">Wiek</th><th>Cena</th><th>MA 20/50</th><th>Dystans</th><th>RSI</th><th>ADX</th><th>Vol/Avg</th></tr>{rows}</table>"""

def main():
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    full_html = f"<html><body style='font-family:Segoe UI,Arial;color:#333;font-size:15px;'><div style='background:#2c3e50;color:white;padding:20px;text-align:center;'><h2>Raport S&P 500 & 600 - {date_str}</h2></div>"
    for name, url in SOURCES.items():
        meta = get_tickers_metadata(url)
        bull, bear = analyze_market(meta)
        full_html += f"<div style='padding:20px;'><h3>📊 Rynek: {name}</h3>{create_sector_summary(bull, bear)}" \
                     f"<h4 style='color:green;font-size:18px;'>🚀 Golden Cross (Bycze)</h4>{create_table_html(bull, 'bullish')}" \
                     f"<h4 style='color:red;font-size:18px;'>📉 Death Cross (Niedźwiedzie)</h4>{create_table_html(bear, 'bearish')}</div>"
    full_html += """<div style='font-size:13px;color:gray;padding:20px;border-top:1px solid #eee;'>
        <b>Legenda kolorów:</b><br>
        - <span style='color:#27ae60;font-weight:bold;'>Zielony ADX/Vol</span>: Silny trend (>25) lub bardzo wysoki obrót (>1.5x).<br>
        - <span style='color:#e67e22;font-weight:bold;'>Pomarańczowy ADX/Vol</span>: Trend budujący się (15-25) lub podwyższony obrót (1.0-1.5x).<br>
        - <b>Dystans</b>: Zielony = zgodny z kierunkiem sygnału, Czerwony = przeciwny.
    </div></body></html>"""
    
    if EMAIL_SENDER and EMAIL_RECIPIENT:
        msg = EmailMessage()
        msg['Subject'] = f"📊 Raport Giełdowy - {date_str}"
        msg['From'], msg['To'] = EMAIL_SENDER, EMAIL_RECIPIENT
        msg.add_alternative(full_html, subtype='html')
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("Wysłano raport.")

if __name__ == "__main__": main()
