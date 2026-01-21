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
        
        df.rename(columns={
            'Security': 'Name', 
            'Company': 'Name', 
            'GICS Sector': 'Sector'
        }, inplace=True)
        
        df['Symbol'] = df['Symbol'].str.replace('.', '-')
        return df.set_index('Symbol')[['Name', 'Sector']].to_dict('index')
    except Exception as e:
        print(f"Błąd pobierania metadanych: {e}")
        return {}

def analyze_market(metadata, lookback_window=5):
    tickers = list(metadata.keys())
    if not tickers: return [], []
    
    data = yf.download(tickers, period="7mo", group_by='ticker', auto_adjust=True, progress=False)
    bullish, bearish = [], []
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue
            df = data[ticker].dropna().copy()
            if len(df) < 60: continue
            
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['VolMA20'] = df['Volume'].rolling(window=20).mean()
            df['RSI'] = ta.rsi(df['Close'], length=14)
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            
            found_type = None
            sessions_ago = 0
            
            for i in range(1, lookback_window + 1):
                today_idx, yesterday_idx = -i, -(i + 1)
                if abs(yesterday_idx) > len(df): break
                t_row, y_row = df.iloc[today_idx], df.iloc[yesterday_idx]
                
                if y_row['MA20'] <= y_row['MA50'] and t_row['MA20'] > t_row['MA50']:
                    found_type, sessions_ago = 'bullish', i - 1
                    break
                elif y_row['MA20'] >= y_row['MA50'] and t_row['MA20'] < t_row['MA50']:
                    found_type, sessions_ago = 'bearish', i - 1
                    break

            if found_type:
                today = df.iloc[-1]
                info = {
                    'ticker': ticker,
                    'name': metadata[ticker].get('Name', 'N/A'),
                    'sector': metadata[ticker].get('Sector', 'N/A'),
                    'close': today['Close'],
                    'ma20': today['MA20'],
                    'ma50': today['MA50'],
                    'dist_ma20': ((today['Close'] - today['MA20']) / today['MA20']) * 100,
                    'rsi': today['RSI'],
                    'adx': adx_df.iloc[-1]['ADX_14'] if adx_df is not None else 0,
                    'vol_ratio': today['Volume'] / today['VolMA20'] if today['VolMA20'] > 0 else 0,
                    'age': sessions_ago
                }
                if found_type == 'bullish': bullish.append(info)
                else: bearish.append(info)
        except: continue
            
    return sorted(bullish, key=lambda x: x['age']), sorted(bearish, key=lambda x: x['age'])

def create_sector_summary(bullish, bearish):
    b_sectors = Counter([s['sector'] for s in bullish])
    d_sectors = Counter([s['sector'] for s in bearish])
    all_sectors = sorted(set(list(b_sectors.keys()) + list(d_sectors.keys())))
    
    if not all_sectors: return ""
    
    rows = ""
    for sector in all_sectors:
        b_count = b_sectors.get(sector, 0)
        d_count = d_sectors.get(sector, 0)
        rows += f"""
        <tr>
            <td style="padding: 4px 10px; border-bottom: 1px solid #eee;">{sector}</td>
            <td style="padding: 4px 10px; border-bottom: 1px solid #eee; text-align: center; color: green;"><b>{b_count if b_count > 0 else '-'}</b></td>
            <td style="padding: 4px 10px; border-bottom: 1px solid #eee; text-align: center; color: red;"><b>{d_count if d_count > 0 else '-'}</b></td>
        </tr>"""
        
    return f"""
    <div style="margin: 10px 0; padding: 10px; background-color: #fcfcfc; border: 1px solid #eee; border-radius: 5px; display: inline-block;">
        <h4 style="margin: 0 0 8px 0; color: #555; font-size: 13px;">Sektory - Podsumowanie:</h4>
        <table style="font-size: 11px; border-collapse: collapse;">
            <tr style="text-align: left; background: #f0f0f0;">
                <th style="padding: 4px 10px;">Sektor</th>
                <th style="padding: 4px 10px;">Golden</th>
                <th style="padding: 4px 10px;">Death</th>
            </tr>
            {rows}
        </table>
    </div>"""

def create_table_html(signals):
    if not signals: return "<p style='color: gray; font-size: 12px;'>Brak sygnałów w oknie 5 dni.</p>"
    
    rows = ""
    for s in signals:
        age_text = "Dzisiaj" if s['age'] == 0 else f"{s['age']}d"
        # Przywrócone style kolorystyczne
        rsi_style = "color: #e67e22; font-weight: bold;" if s['rsi'] > 70 or s['rsi'] < 30 else ""
        vol_style = "color: #27ae60; font-weight: bold;" if s['vol_ratio'] > 1.5 else ""
        dist_style = "color: #e74c3c;" if abs(s['dist_ma20']) > 5 else ""

        rows += f"""
        <tr style="border-bottom: 1px solid #eee; font-size: 12px;">
            <td style="padding: 8px;"><b>{s['ticker']}</b></td>
            <td style="padding: 8px; font-size: 11px;">{s['name']}</td>
            <td style="padding: 8px; font-size: 10px; color: #666;">{s['sector']}</td>
            <td style="padding: 8px; text-align: center;">{age_text}</td>
            <td style="padding: 8px;"><b>{s['close']:.2f}</b></td>
            <td style="padding: 8px; font-size: 11px; color: #444;">{s['ma20']:.1f} / {s['ma50']:.1f}</td>
            <td style="padding: 8px; {dist_style}">{s['dist_ma20']:+.1f}%</td>
            <td style="padding: 8px; {rsi_style}">{s['rsi']:.1f}</td>
            <td style="padding: 8px;">{s['adx']:.1f}</td>
            <td style="padding: 8px; {vol_style}">{s['vol_ratio']:.2f}x</td>
        </tr>"""
        
    return f"""
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px;">
        <tr style="background-color: #f8f9fa; text-align: left; border-bottom: 2px solid #dee2e6; font-size: 11px;">
            <th style="padding: 8px;">Ticker</th><th style="padding: 8px;">Nazwa</th><th style="padding: 8px;">Sektor</th>
            <th style="padding: 8px; text-align: center;">Wiek</th><th style="padding: 8px;">Cena</th>
            <th style="padding: 8px;">MA 20/50</th><th style="padding: 8px;">Dystans</th>
            <th style="padding: 8px;">RSI</th><th style="padding: 8px;">ADX</th><th style="padding: 8px;">Vol/Avg</th>
        </tr>
        {rows}
    </table>"""

def main():
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    full_report_html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.4;">
        <div style="background-color: #2c3e50; color: white; padding: 15px; text-align: center;">
            <h2 style="margin: 0;">Raport S&P 500 & 600 - {date_str}</h2>
        </div>"""

    for name, url in SOURCES.items():
        print(f"Przetwarzanie: {name}")
        metadata = get_tickers_metadata(url)
        bullish, bearish = analyze_market(metadata)
        
        full_report_html += f"""
        <div style="padding: 15px; border-bottom: 3px solid #eee;">
            <h3 style="color: #2c3e50; margin-bottom: 5px;">📊 Rynek: {name}</h3>
            {create_sector_summary(bullish, bearish)}
            <h4 style="color: #27ae60; margin-bottom: 5px; margin-top: 15px;">🚀 Golden Cross (Bycze)</h4>
            {create_table_html(bullish)}
            <h4 style="color: #c0392b; margin-bottom: 5px; margin-top: 15px;">📉 Death Cross (Niedźwiedzie)</h4>
            {create_table_html(bearish)}
        </div>"""

    full_report_html += """
        <div style="font-size: 10px; color: gray; padding: 20px;">
            <b>Legenda kolorów:</b><br>
            - <span style="color: #e67e22; font-weight: bold;">Pomarańczowy RSI:</span> Spółka wykupiona (>70) lub wyprzedana (<30).<br>
            - <span style="color: #27ae60; font-weight: bold;">Zielony Vol/Avg:</span> Wolumen ponad 1.5x większy od średniej.<br>
            - <span style="color: #e74c3c;">Czerwony Dystans:</span> Cena oddalona o ponad 5% od MA20.
        </div>
    </body></html>"""

    if EMAIL_SENDER and EMAIL_RECIPIENT:
        msg = EmailMessage()
        msg['Subject'] = f"📊 Raport Giełdowy S&P 500/600 - {date_str}"
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg.add_alternative(full_report_html, subtype='html')
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("Raport wysłany.")

if __name__ == "__main__":
    main()
