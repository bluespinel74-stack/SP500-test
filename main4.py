import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import os
import datetime
import sys
import requests
import time
import gc
from io import StringIO
from email.message import EmailMessage

# --- KONFIGURACJA ---
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465

# --- URL WIKIPEDII (S&P 600) ---
WIKI_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies'

# --- PARAMETRY STRATEGII ---
MIN_ADX = 20           # Siła trendu (mniej restrykcyjna)
MIN_RVOL = 1.0         # Relative Volume
MAX_RSI_LONG = 70      # Bezpieczny poziom wejścia
MIN_RSI_SHORT = 30     

# --- FILTRY PŁYNNOŚCI (Dla małych spółek) ---
MIN_PRICE = 5.0        # Odrzucamy groszowe (< $5)
MIN_AVG_VOLUME = 50000 # Odrzucamy martwe (< 50k obrotu)

def get_sp600_tickers():
    print(f"Pobieranie listy S&P 600 z Wikipedii...")
    try:
        # Udajemy przeglądarkę
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        
        response = requests.get(WIKI_URL, headers=headers)
        response.raise_for_status()
        
        # Pandas parsuje HTML
        tables = pd.read_html(StringIO(response.text))
        
        # Zazwyczaj pierwsza tabela to lista spółek
        df = tables[0]
        
        # Szukamy kolumny Symbol
        tickers = df['Symbol'].tolist()
        
        # Zamiana kropek na myślniki (np. BRK.B -> BRK-B) dla Yahoo Finance
        tickers = [t.replace('.', '-') for t in tickers]
        
        print(f"Sukces! Pobrano {len(tickers)} tickerów.")
        return tickers
    except Exception as e:
        print(f"Krytyczny błąd pobierania listy z Wikipedii: {e}")
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
    
    div = plus_di + minus_di
    div = div.replace(0, 1)
    
    dx = 100 * abs(plus_di - minus_di) / div
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return adx

def process_batch(tickers_batch):
    bullish = []
    bearish = []
    
    try:
        # Pobieranie danych
        data = yf.download(tickers_batch, period="6mo", group_by='ticker', auto_adjust=True, progress=False, threads=True)
        
        # Obsługa przypadku 1 tickera w paczce
        if len(tickers_batch) == 1: pass

        for ticker in tickers_batch:
            try:
                # Obsługa struktury DataFrame (MultiIndex vs Single)
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    if len(tickers_batch) == 1: df = data.copy()
                    else: continue
                
                # Czyszczenie danych
                df.dropna(inplace=True)
                if len(df) < 60: continue

                # --- FILTRY PŁYNNOŚCI ---
                current_price = df['Close'].iloc[-1]
                avg_vol = df['Volume'].rolling(window=20).mean().iloc[-1]

                if current_price < MIN_PRICE: continue
                if avg_vol < MIN_AVG_VOLUME: continue

                # --- WSKAŹNIKI ---
                df['MA20'] = df['Close'].rolling(window=20).mean()
                df['MA50'] = df['Close'].rolling(window=50).mean()
                df['RSI'] = calculate_rsi(df['Close'])
                df['ADX'] = calculate_adx(df)
                df['VolMA20'] = df['Volume'].rolling(window=20).mean()

                today = df.iloc[-1]
                yesterday = df.iloc[-2]

                if pd.isna(today['ADX']) or pd.isna(yesterday['ADX']): continue

                # --- WARUNEK ADX ---
                # ADX > 20 ORAZ (Rośnie LUB jest bardzo silny > 30)
                is_adx_ok = (today['ADX'] > MIN_ADX) and (today['ADX'] > yesterday['ADX'] or today['ADX'] > 30)
                if not is_adx_ok: continue

                vol_ratio = 0
                if today['VolMA20'] > 0:
                    vol_ratio = today['Volume'] / today['VolMA20']

                # --- SYGNAŁY ---
                # Golden Cross
                if (yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']):
                    if today['RSI'] <= MAX_RSI_LONG:
                        bullish.append({
                            'ticker': ticker, 'close': today['Close'],
                            'adx': today['ADX'], 'rsi': today['RSI'], 'vol_ratio': vol_ratio
                        })

                # Death Cross
                elif (yesterday['MA20'] >= yesterday['MA50'] and today['MA20'] < today['MA50']):
                    if today['RSI'] >= MIN_RSI_SHORT:
                        bearish.append({
                            'ticker': ticker, 'close': today['Close'],
                            'adx': today['ADX'], 'rsi': today['RSI'], 'vol_ratio': vol_ratio
                        })
            except Exception:
                continue
                
    except Exception as e:
        print(f"Błąd w paczce danych: {e}")
        
    return bullish, bearish

def send_email_alert(bullish, bearish):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        print(f"--- TRYB TESTOWY (Brak maila) ---")
        print(f"Znaleziono: {len(bullish)} Byczych, {len(bearish)} Niedźwiedzich.")
        if bullish: print(f"Przykładowe Bycze: {[x['ticker'] for x in bullish[:5]]}")
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"Raport S&P 600 (Small Cap) - {date_str}"
    
    bullish.sort(key=lambda x: x['adx'], reverse=True)
    bearish.sort(key=lambda x: x['adx'], reverse=True)
    
    # Top 25 dla czytelności
    top_bullish = bullish[:25]
    top_bearish = bearish[:25]

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Raport S&P 600 (Small Cap)</h2>
        <p>Data: <b>{date_str}</b></p>
        <div style="font-size: small; color: #555; background-color: #f4f4f4; padding: 10px;">
            <b>Filtry:</b> Cena > ${MIN_PRICE}, Vol > {MIN_AVG_VOLUME/1000:.0f}k<br>
            <b>Strategia:</b> MA20/50 Cross + ADX > {MIN_ADX} + RSI 30-70.
        </div>
        <hr>
    """
    
    if not top_bullish and not top_bearish:
        html_content += "<p>Brak sygnałów spełniających kryteria.</p>"
    
    if top_bullish:
        html_content += f"<h3 style='color: green;'>🚀 Top {len(top_bullish)} Golden Cross</h3><ul>"
        for item in top_bullish:
            strong_vol = item['vol_ratio'] > 1.5
            bg = "background-color: #e6fffa;" if strong_vol else ""
            html_content += f"<li style='{bg}'><b>{item['ticker']}</b> (${item['close']:.2f}) - ADX: {item['adx']:.1f}, Vol: {item['vol_ratio']:.1f}x</li>"
        html_content += "</ul>"

    if top_bearish:
        html_content += f"<h3 style='color: red;'>📉 Top {len(top_bearish)} Death Cross</h3><ul>"
        for item in top_bearish:
            strong_vol = item['vol_ratio'] > 1.5
            bg = "background-color: #fff5f5;" if strong_vol else ""
            html_content += f"<li style='{bg}'><b>{item['ticker']}</b> (${item['close']:.2f}) - ADX: {item['adx']:.1f}, Vol: {item['vol_ratio']:.1f}x</li>"
        html_content += "</ul>"

    html_content += "</body></html>"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECIPIENT
    msg.set_content("HTML required.")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("E-mail wysłany pomyślnie.")
    except Exception as e:
        print(f"Błąd wysyłki: {e}")

def main():
    tickers = get_sp600_tickers()
    
    # Batching jest nadal ważny, żeby nie przeciążyć API przy 600 spółkach
    BATCH_SIZE = 100
    total_bullish = []
    total_bearish = []
    
    print(f"Analiza {len(tickers)} spółek w paczkach po {BATCH_SIZE}...")
    
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        print(f"Przetwarzanie {i} do {i + len(batch)}...")
        
        b_bull, b_bear = process_batch(batch)
        total_bullish.extend(b_bull)
        total_bearish.extend(b_bear)
        
        time.sleep(1) 
        gc.collect() 

    print(f"Koniec. Znaleziono: {len(total_bullish)} Byczych, {len(total_bearish)} Niedźwiedzich.")
    send_email_alert(total_bullish, total_bearish)

if __name__ == "__main__":
    main()
