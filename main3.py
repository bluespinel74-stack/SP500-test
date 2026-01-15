import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import os
import datetime
import sys
import requests
from io import StringIO
from email.message import EmailMessage

# Konfiguracja
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465
WIKI_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

# --- ZMODYFIKOWANE PARAMETRY (MNIEJ RESTRYKCYJNE) ---
MIN_RVOL = 1.0         # Wolumen musi być po prostu równy lub wyższy od średniej (było 1.1)
# Kryteria ADX
MIN_ADX = 20           # Obniżono z 25 na 20 (łapiemy wcześniejsze fazy trendu)
# Kryteria RSI
MAX_RSI_LONG = 70      # Podniesiono z 65 (standardowy poziom wykupienia)
MIN_RSI_SHORT = 30     # Obniżono z 35 (standardowy poziom wyprzedania)

def get_sp500_tickers():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}
        print(f"Pobieranie listy spółek z: {WIKI_URL}")
        response = requests.get(WIKI_URL, headers=headers)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        tickers = df['Symbol'].tolist()
        tickers = [ticker.replace('.', '-') for ticker in tickers]
        print(f"Pobrano {len(tickers)} tickerów z S&P 500.")
        return tickers
    except Exception as e:
        print(f"Krytyczny błąd podczas pobierania listy tickerów: {e}")
        sys.exit(1)

def fetch_data(tickers):
    print("Rozpoczynanie pobierania danych (OHLCV)...")
    try:
        data = yf.download(tickers, period="6mo", group_by='ticker', auto_adjust=True, progress=False, threads=True)
        return data
    except Exception as e:
        print(f"Błąd podczas pobierania danych: {e}")
        sys.exit(1)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_adx(df, period=14):
    plus_dm = df['High'].diff()
    minus_dm = df['Low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return adx

def calculate_signals(data, tickers):
    bullish_signals = []
    bearish_signals = []
    
    print(f"Analiza wskaźników (ADX > {MIN_ADX}, RSI < {MAX_RSI_LONG})...")
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue

            df = data[ticker].dropna()
            if len(df) < 60: continue

            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['RSI'] = calculate_rsi(df['Close'])
            df['ADX'] = calculate_adx(df)
            df['VolMA20'] = df['Volume'].rolling(window=20).mean()

            today = df.iloc[-1]
            yesterday = df.iloc[-2]

            if (pd.isna(today['MA20']) or pd.isna(today['MA50']) or 
                pd.isna(today['RSI']) or pd.isna(today['ADX']) or 
                pd.isna(yesterday['ADX'])):
                continue

            # --- ZMODYFIKOWANY FILTR ADX ---
            # Warunek: ADX musi być powyżej 20 ORAZ (rosnąć LUB być bardzo silny > 30)
            # To pozwala na wejście, gdy trend jest silny, nawet jeśli lekko wyhamował jednodniowo.
            is_adx_ok = (today['ADX'] > MIN_ADX) and (today['ADX'] > yesterday['ADX'] or today['ADX'] > 30)
            
            if not is_adx_ok:
                continue 

            vol_ratio = 0
            if today['VolMA20'] > 0:
                vol_ratio = today['Volume'] / today['VolMA20']
            
            # GOLDEN CROSS
            if (yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']):
                if today['RSI'] <= MAX_RSI_LONG: # Limit 70
                    bullish_signals.append({
                        'ticker': ticker,
                        'close': today['Close'],
                        'ma20': today['MA20'],
                        'ma50': today['MA50'],
                        'rsi': today['RSI'],
                        'adx': today['ADX'],
                        'vol_ratio': vol_ratio
                    })

            # DEATH CROSS
            elif (yesterday['MA20'] >= yesterday['MA50'] and today['MA20'] < today['MA50']):
                if today['RSI'] >= MIN_RSI_SHORT: # Limit 30
                    bearish_signals.append({
                        'ticker': ticker,
                        'close': today['Close'],
                        'ma20': today['MA20'],
                        'ma50': today['MA50'],
                        'rsi': today['RSI'],
                        'adx': today['ADX'],
                        'vol_ratio': vol_ratio
                    })

        except Exception:
            continue
            
    return bullish_signals, bearish_signals

def send_email_alert(bullish, bearish):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        print("Brak danych SMTP. Brak wysyłki.")
        # Drukujemy na ekranie, jeśli brak maila (do testów)
        print("Bullish:", [x['ticker'] for x in bullish])
        print("Bearish:", [x['ticker'] for x in bearish])
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"S&P 500 Signals (ADX>{MIN_ADX}) - {date_str}"
    
    bullish.sort(key=lambda x: x['adx'], reverse=True)
    bearish.sort(key=lambda x: x['adx'], reverse=True)

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Sygnały S&P 500 (Standard Filter)</h2>
        <p>Data: <b>{date_str}</b></p>
        <div style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-size: small; color: #555;">
            <b>Parametry:</b> ADX > {MIN_ADX} | RSI: 30-70 | MA20/MA50 Crossover
        </div>
        <hr>
    """
    
    if not bullish and not bearish:
        html_content += "<p>Brak sygnałów. Rynek może być w fazie silnej konsolidacji.</p>"
    
    if bullish:
        html_content += "<h3 style='color: green;'>🚀 Golden Cross (Kupno)</h3><ul>"
        for item in bullish:
            # Pogrubiamy jeśli vol_ratio > 1.2 (nieco wyższy próg dla wyróżnienia)
            strong_vol = item['vol_ratio'] > 1.2
            bg_style = "background-color: #e6fffa;" if strong_vol else ""
            
            html_content += f"""
            <li style="margin-bottom: 8px; padding: 5px; {bg_style}">
                <b>{item['ticker']}</b> (${item['close']:.2f})<br>
                <span style="font-size: small; color: #333;">
                   ADX: <b>{item['adx']:.1f}</b> | RSI: {item['rsi']:.1f} | Vol: {item['vol_ratio']:.2f}x
                </span>
            </li>"""
        html_content += "</ul>"
        
    if bearish:
        html_content += "<h3 style='color: red;'>📉 Death Cross (Sprzedaż)</h3><ul>"
        for item in bearish:
            strong_vol = item['vol_ratio'] > 1.2
            bg_style = "background-color: #fff5f5;" if strong_vol else ""

            html_content += f"""
            <li style="margin-bottom: 8px; padding: 5px; {bg_style}">
                <b>{item['ticker']}</b> (${item['close']:.2f})<br>
                <span style="font-size: small; color: #333;">
                   ADX: <b>{item['adx']:.1f}</b> | RSI: {item['rsi']:.1f} | Vol: {item['vol_ratio']:.2f}x
                </span>
            </li>"""
        html_content += "</ul>"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECIPIENT
    msg.set_content("Wymagany klient HTML.")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("E-mail wysłany.")
    except Exception as e:
        print(f"Błąd wysyłki: {e}")

def main():
    tickers = get_sp500_tickers()
    if not tickers: return

    data = fetch_data(tickers)
    if data is None or data.empty: return

    bullish, bearish = calculate_signals(data, tickers)
    print(f"Wynik: {len(bullish)} Byczych, {len(bearish)} Niedźwiedzich.")
    send_email_alert(bullish, bearish)

if __name__ == "__main__":
    main()
