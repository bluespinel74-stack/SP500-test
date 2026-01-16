import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import os
import datetime
import sys
import requests
import time
import gc # Garbage Collector do zwalniania pamięci
from io import StringIO
from email.message import EmailMessage

# --- KONFIGURACJA ---
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465

# --- PARAMETRY STRATEGII ---
MIN_ADX = 20           # Siła trendu
MIN_RVOL = 1.0         # Relative Volume
MAX_RSI_LONG = 70      
MIN_RSI_SHORT = 30     

# --- PARAMETRY FILTRACJI ŚMIECIOWYCH SPÓŁEK (NOWOŚĆ) ---
MIN_PRICE = 5.0        # Odrzucamy spółki tańsze niż $5 (Penny Stocks)
MIN_AVG_VOLUME = 50000 # Minimalny średni wolumen (płynność)

# Źródło tickerów (Listy z GitHub są zazwyczaj najbardziej aktualne dla R2000 za darmo)
RUSSELL_URL = 'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/russell2000/russell2000_tickers.txt'

def get_russell2000_tickers():
    print("Pobieranie listy tickerów Russell 2000...")
    try:
        response = requests.get(RUSSELL_URL)
        response.raise_for_status()
        # Zakładamy, że tickery są w pliku tekstowym, jeden pod drugim lub po przecinku
        content = response.text.replace('\n', ',').replace(';', ',')
        tickers = [t.strip() for t in content.split(',') if t.strip()]
        
        # Czyszczenie tickerów (zamiana kropki na myślnik dla Yahoo, np. BRK.B -> BRK-B)
        tickers = [ticker.replace('.', '-') for ticker in tickers]
        
        # Limit bezpieczeństwa (gdyby lista była pusta)
        if len(tickers) < 100:
            raise ValueError("Pobrano podejrzanie mało tickerów.")
            
        print(f"Załadowano {len(tickers)} tickerów.")
        return tickers
    except Exception as e:
        print(f"Błąd pobierania listy: {e}")
        # Fallback: Jeśli link padnie, tu można wstawić ręczną listę lub inne źródło
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
    
    # Zabezpieczenie przed dzieleniem przez zero
    div = plus_di + minus_di
    div = div.replace(0, 1)
    
    dx = 100 * abs(plus_di - minus_di) / div
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return adx

def process_batch(tickers_batch):
    """Przetwarza małą paczkę tickerów i zwraca sygnały."""
    bullish = []
    bearish = []
    
    try:
        # Pobieranie danych tylko dla paczki (np. 100 sztuk)
        data = yf.download(tickers_batch, period="6mo", group_by='ticker', auto_adjust=True, progress=False, threads=True)
        
        # Jeśli pobraliśmy tylko 1 ticker, struktura DataFrame jest inna, naprawiamy to:
        if len(tickers_batch) == 1:
            # yfinance dla 1 tickera nie robi MultiIndex w kolumnach
            # Musimy to obsłużyć, ale przy batch=100 rzadko wystąpi.
            # Dla uproszczenia w pętli iterujemy po tickerach z listy.
            pass 

        for ticker in tickers_batch:
            try:
                # Obsługa różnych struktur yfinance
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    # Przypadek gdy pobrano tylko 1 firmę poprawnie
                    if len(tickers_batch) == 1: df = data.copy()
                    else: continue
                
                df.dropna(inplace=True)
                if len(df) < 60: continue

                # --- NOWE FILTRY PŁYNNOŚCI ---
                current_price = df['Close'].iloc[-1]
                avg_vol = df['Volume'].rolling(window=20).mean().iloc[-1]

                if current_price < MIN_PRICE: continue      # Odrzuć groszowe
                if avg_vol < MIN_AVG_VOLUME: continue       # Odrzuć martwe spółki

                # Wskaźniki
                df['MA20'] = df['Close'].rolling(window=20).mean()
                df['MA50'] = df['Close'].rolling(window=50).mean()
                df['RSI'] = calculate_rsi(df['Close'])
                df['ADX'] = calculate_adx(df)
                df['VolMA20'] = df['Volume'].rolling(window=20).mean()

                today = df.iloc[-1]
                yesterday = df.iloc[-2]

                if pd.isna(today['ADX']) or pd.isna(yesterday['ADX']): continue

                # Filtr ADX (mniej restrykcyjny)
                is_adx_ok = (today['ADX'] > MIN_ADX) and (today['ADX'] > yesterday['ADX'] or today['ADX'] > 30)
                if not is_adx_ok: continue

                vol_ratio = 0
                if today['VolMA20'] > 0:
                    vol_ratio = today['Volume'] / today['VolMA20']

                # Sygnały
                if (yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']):
                    if today['RSI'] <= MAX_RSI_LONG:
                        bullish.append({
                            'ticker': ticker, 'close': today['Close'],
                            'adx': today['ADX'], 'rsi': today['RSI'], 'vol_ratio': vol_ratio
                        })

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
        print(f"Symulacja wysyłki. Bycze: {len(bullish)}, Niedźwiedzie: {len(bearish)}")
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"Russell 2000 Report - {date_str}"
    
    bullish.sort(key=lambda x: x['adx'], reverse=True)
    bearish.sort(key=lambda x: x['adx'], reverse=True)
    
    # Skracamy listę do TOP 20, żeby nie zapchać maila przy R2000
    top_bullish = bullish[:20]
    top_bearish = bearish[:20]

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Raport Russell 2000 (Small Caps)</h2>
        <p>Data: <b>{date_str}</b> | Przeskanowano ~2000 spółek.</p>
        <div style="font-size: small; color: #555;">
            Filtry: Cena > ${MIN_PRICE}, AvgVol > {MIN_AVG_VOLUME/1000:.0f}k<br>
            ADX > {MIN_ADX}, RSI 30-70.
        </div>
        <hr>
    """
    
    if not top_bullish and not top_bearish:
        html_content += "<p>Brak silnych sygnałów na płynnych spółkach.</p>"
    
    if top_bullish:
        html_content += f"<h3 style='color: green;'>🚀 Top {len(top_bullish)} Golden Cross</h3><ul>"
        for item in top_bullish:
            strong_vol = item['vol_ratio'] > 1.5
            bg = "background-color: #e6fffa;" if strong_vol else ""
            html_content += f"<li style='{bg}'><b>{item['ticker']}</b> (${item['close']:.2f}) - ADX: {item['adx']:.1f}, Vol: {item['vol_ratio']:.1f}x</li>"
        html_content += "</ul>"
        if len(bullish) > 20: html_content += f"<p>...i {len(bullish)-20} więcej.</p>"

    if top_bearish:
        html_content += f"<h3 style='color: red;'>📉 Top {len(top_bearish)} Death Cross</h3><ul>"
        for item in top_bearish:
            strong_vol = item['vol_ratio'] > 1.5
            bg = "background-color: #fff5f5;" if strong_vol else ""
            html_content += f"<li style='{bg}'><b>{item['ticker']}</b> (${item['close']:.2f}) - ADX: {item['adx']:.1f}, Vol: {item['vol_ratio']:.1f}x</li>"
        html_content += "</ul>"
        if len(bearish) > 20: html_content += f"<p>...i {len(bearish)-20} więcej.</p>"

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
            print("E-mail wysłany.")
    except Exception as e:
        print(f"Błąd wysyłki: {e}")

def main():
    all_tickers = get_russell2000_tickers()
    
    # --- BATCH PROCESSING (Kluczowe dla dużej liczby spółek) ---
    BATCH_SIZE = 100
    total_bullish = []
    total_bearish = []
    
    print(f"Rozpoczynanie analizy w paczkach po {BATCH_SIZE}...")
    
    for i in range(0, len(all_tickers), BATCH_SIZE):
        batch = all_tickers[i:i + BATCH_SIZE]
        print(f"Przetwarzanie {i} do {i + len(batch)}...")
        
        b_bull, b_bear = process_batch(batch)
        total_bullish.extend(b_bull)
        total_bearish.extend(b_bear)
        
        # Mała pauza, żeby Yahoo nas nie zbanowało
        time.sleep(1) 
        gc.collect() # Czyść pamięć

    print(f"Koniec. Znaleziono: {len(total_bullish)} Byczych, {len(total_bearish)} Niedźwiedzich.")
    send_email_alert(total_bullish, total_bearish)

if __name__ == "__main__":
    main()
