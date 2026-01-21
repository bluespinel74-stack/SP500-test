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

def analyze_market(tickers):
    if not tickers: return [], []
    
    # Pobieranie danych (7 miesięcy dla poprawnego wyliczenia wskaźników)
    data = yf.download(tickers, period="7mo", group_by='ticker', auto_adjust=True, progress=False)
    bullish, bearish = [], []
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue
            df = data[ticker].dropna().copy()
            if len(df) < 60: continue
            
            # Obliczenia techniczne
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['VolMA20'] = df['Volume'].rolling(window=20).mean() # Średni wolumen
            df['RSI'] = ta.rsi(df['Close'], length=14)
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            
            # Nowe metryki: relacja wolumenu i odległość od MA20
            vol_ratio = today['Volume'] / today['VolMA20'] if today['VolMA20'] > 0 else 0
            dist_ma20 = ((today['Close'] - today['MA20']) / today['MA20']) * 100
            
            info = {
                'ticker': ticker,
                'close': today['Close'],
                'ma20': today['MA20'],
                'dist_ma20': dist_ma20,
                'rsi': today['RSI'],
                'adx': adx_df.iloc[-1]['ADX_14'] if adx_df is not None else 0,
                'volume': today['Volume'],
                'vol_ratio': vol_ratio
            }

            # Logika przecięcia średnich (Golden/Death Cross)
            if yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']:
                bullish.append(info)
            elif yesterday['MA20'] >= yesterday['MA50'] and today['MA20'] < today['MA50']:
                bearish.append(info)
        except:
            continue
    return bullish, bearish

def create_table_html(signals):
    if not signals: return "<p style='color: gray;'>Brak sygnałów dla tej grupy.</p>"
    
    rows = ""
    for s in signals:
        # Style dla wyróżnienia danych
        rsi_style = "color: #e67e22; font-weight: bold;" if s['rsi'] > 70 or s['rsi'] < 30 else ""
        vol_style = "color: #27ae60; font-weight: bold;" if s['vol_ratio'] > 1.5 else "" # Wysoki wolumen
        dist_style = "color: #e74c3c;" if abs(s['dist_ma20']) > 5 else "" # Duże odchylenie od MA20
        
        rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px;"><b>{s['ticker']}</b></td>
            <td style="padding: 10px;">{s['close']:.2f}</td>
            <td style="padding: 10px; {dist_style}">{s['dist_ma20']:+.2f}%</td>
            <td style="padding: 10px; {rsi_style}">{s['rsi']:.1f}</td>
            <td style="padding: 10px;">{s['adx']:.1f}</td>
            <td style="padding: 10px;">{s['volume']:,.0f}</td>
            <td style="padding: 10px; {vol_style}">{s['vol_ratio']:.2f}x</td>
        </tr>
        """
    return f"""
    <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 20px;">
        <tr style="background-color: #f8f9fa; text-align: left; border-bottom: 2px solid #dee2e6;">
            <th style="padding: 10px;">Ticker</th>
            <th style="padding: 10px;">Cena</th>
            <th style="padding: 10px;">Dystans MA20</th>
            <th style="padding: 10px;">RSI (14)</th>
            <th style="padding: 10px;">ADX (14)</th>
            <th style="padding: 10px;">Wolumen</th>
            <th style="padding: 10px;">Vol/Avg</th>
        </tr>
        {rows}
    </table>
    """

def main():
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    full_report_html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.5;">
        <div style="background-color: #2c3e50; color: white; padding: 20px; text-align: center;">
            <h1 style="margin: 0;">Raport S&P 500 & 600</h1>
            <p style="margin: 5px 0 0 0;">Analiza sesji z dnia {date_str}</p>
        </div>
        <div style="padding: 20px;">
    """

    for name, url in SOURCES.items():
        print(f"Przetwarzanie {name}...")
        tickers = get_tickers(url)
        bullish, bearish = analyze_market(tickers)
        
        full_report_html += f"""
            <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 5px; margin-top: 40px;">📊 Rynek: {name}</h2>
            
            <h3 style="color: #27ae60;">🚀 Golden Cross (MA20 ↑ MA50)</h3>
            {create_table_html(bullish)}
            
            <h3 style="color: #c0392b;">📉 Death Cross (MA20 ↓ MA50)</h3>
            {create_table_html(bearish)}
        """

    full_report_html += """
        </div>
        <div style="background-color: #f8f9fa; padding: 15px; font-size: 11px; color: #7f8c8d; border-top: 1px solid #eee;">
            <p><b>Legenda:</b><br>
            - <b>Dystans MA20:</b> Procentowa odległość ceny od średniej 20-dniowej.<br>
            - <b>ADX > 25:</b> Sygnalizuje silny trend.<br>
            - <b>Vol/Avg:</b> Stosunek dzisiejszego wolumenu do średniej z 20 dni (wartości > 1.5x wyróżnione na zielono).<br>
            - <b>RSI:</b> Wartości < 30 (wyprzedanie) lub > 70 (wykupienie) wyróżnione na pomarańczowo.</p>
        </div>
    </body>
    </html>
    """

    if EMAIL_SENDER and EMAIL_RECIPIENT:
        msg = EmailMessage()
        msg['Subject'] = f"📈 Raport S&P 500/600: {date_str}"
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg.add_alternative(full_report_html, subtype='html')
        
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
                smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
                smtp.send_message(msg)
                print("E-mail wysłany pomyślnie.")
        except Exception as e:
            print(f"Błąd wysyłki: {e}")

if __name__ == "__main__":
    main()
