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

def get_tickers(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        df = pd.read_html(StringIO(response.text), flavor='lxml')[0]
        tickers = df['Symbol'].tolist()
        return [t.replace('.', '-') for t in tickers]
    except Exception as e:
        print(f"Błąd pobierania tickerów z {url}: {e}")
        return []

def analyze_market(tickers, lookback_window=5):
    if not tickers: return [], []
    
    data = yf.download(tickers, period="7mo", group_by='ticker', auto_adjust=True, progress=False)
    bullish, bearish = [], []
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue
            df = data[ticker].dropna().copy()
            if len(df) < 60: continue
            
            # Wskaźniki
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['VolMA20'] = df['Volume'].rolling(window=20).mean()
            df['RSI'] = ta.rsi(df['Close'], length=14)
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            
            # Szukanie sygnału w ostatnich X dniach
            found_type = None
            sessions_ago = 0
            
            for i in range(1, lookback_window + 1):
                today_idx = -i
                yesterday_idx = -(i + 1)
                
                if abs(yesterday_idx) > len(df): break
                
                t_row = df.iloc[today_idx]
                y_row = df.iloc[yesterday_idx]
                
                # Golden Cross
                if y_row['MA20'] <= y_row['MA50'] and t_row['MA20'] > t_row['MA50']:
                    found_type = 'bullish'
                    sessions_ago = i - 1
                    break
                # Death Cross
                elif y_row['MA20'] >= y_row['MA50'] and t_row['MA20'] < t_row['MA50']:
                    found_type = 'bearish'
                    sessions_ago = i - 1
                    break

            if found_type:
                today = df.iloc[-1]
                adx_val = adx_df.iloc[-1]['ADX_14'] if adx_df is not None else 0
                
                info = {
                    'ticker': ticker,
                    'close': today['Close'],
                    'ma20': today['MA20'],
                    'ma50': today['MA50'],
                    'dist_ma20': ((today['Close'] - today['MA20']) / today['MA20']) * 100,
                    'rsi': today['RSI'],
                    'adx': adx_val,
                    'vol_ratio': today['Volume'] / today['VolMA20'] if today['VolMA20'] > 0 else 0,
                    'age': sessions_ago
                }
                
                if found_type == 'bullish': bullish.append(info)
                else: bearish.append(info)
        except:
            continue
            
    # Sortowanie: najświeższe sygnały na górze
    bullish.sort(key=lambda x: x['age'])
    bearish.sort(key=lambda x: x['age'])
    return bullish, bearish

def create_table_html(signals):
    if not signals: return "<p style='color: gray;'>Brak nowych sygnałów (ostatnie 5 sesji).</p>"
    
    rows = ""
    for s in signals:
        # Formatowanie wieku
        age_text = "Dzisiaj" if s['age'] == 0 else f"{s['age']} sesje temu"
        age_style = "font-weight: bold; color: #2c3e50;" if s['age'] == 0 else "color: #7f8c8d;"
        
        rsi_style = "color: #e67e22; font-weight: bold;" if s['rsi'] > 70 or s['rsi'] < 30 else ""
        vol_style = "color: #27ae60; font-weight: bold;" if s['vol_ratio'] > 1.5 else ""
        
        rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px;"><b>{s['ticker']}</b></td>
            <td style="padding: 10px; {age_style}">{age_text}</td>
            <td style="padding: 10px;">{s['close']:.2f}</td>
            <td style="padding: 10px; color: #444;">{s['ma20']:.2f}</td>
            <td style="padding: 10px; color: #444;">{s['ma50']:.2f}</td>
            <td style="padding: 10px;">{s['dist_ma20']:+.2f}%</td>
            <td style="padding: 10px; {rsi_style}">{s['rsi']:.1f}</td>
            <td style="padding: 10px;">{s['adx']:.1f}</td>
            <td style="padding: 10px; {vol_style}">{s['vol_ratio']:.2f}x</td>
        </tr>
        """
    return f"""
    <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 25px;">
        <tr style="background-color: #f8f9fa; text-align: left; border-bottom: 2px solid #dee2e6;">
            <th style="padding: 10px;">Ticker</th>
            <th style="padding: 10px;">Wiek sygnału</th>
            <th style="padding: 10px;">Cena</th>
            <th style="padding: 10px;">MA20</th>
            <th style="padding: 10px;">MA50</th>
            <th style="padding: 10px;">Dystans MA20</th>
            <th style="padding: 10px;">RSI (14)</th>
            <th style="padding: 10px;">ADX (14)</th>
            <th style="padding: 10px;">Vol/Avg</th>
        </tr>
        {rows}
    </table>
    """

def main():
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    full_report_html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.5;">
        <div style="background-color: #2c3e50; color: white; padding: 20px; text-align: center;">
            <h1 style="margin: 0;">Raport S&P 500 & 600</h1>
            <p style="margin: 5px 0 0 0;">Analiza z dnia {date_str} (okno 5 sesji)</p>
        </div>
        <div style="padding: 20px;">
    """

    for name, url in SOURCES.items():
        print(f"Analiza rynku: {name}...")
        tickers = get_tickers(url)
        bullish, bearish = analyze_market(tickers)
        
        full_report_html += f"""
            <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 5px; margin-top: 30px;">📊 Rynek: {name}</h2>
            <h3 style="color: #27ae60;">🚀 Golden Cross (MA20 > MA50)</h3>
            {create_table_html(bullish)}
            <h3 style="color: #c0392b;">📉 Death Cross (MA20 < MA50)</h3>
            {create_table_html(bearish)}
        """

    full_report_html += """
        </div>
        <div style="background-color: #f8f9fa; padding: 15px; font-size: 11px; color: #7f8c8d; border-top: 1px solid #eee;">
            <p><b>Legenda:</b><br>
            - <b>Wiek sygnału:</b> Ile sesji temu nastąpiło przecięcie (0 = sesja dzisiejsza).<br>
            - <b>MA20/MA50:</b> Aktualne wartości średnich kroczących.<br>
            - <b>Dystans MA20:</b> Procentowa odległość aktualnej ceny od średniej 20-dniowej.</p>
        </div>
    </body>
    </html>
    """

    if EMAIL_SENDER and EMAIL_RECIPIENT:
        msg = EmailMessage()
        msg['Subject'] = f"📊 Raport Sygnałów S&P 500/600 - {date_str}"
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg.add_alternative(full_report_html, subtype='html')
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("Raport z dodatkowymi danymi wysłany.")

if __name__ == "__main__":
    main()
