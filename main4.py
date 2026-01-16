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

# --- PARAMETRY STRATEGII ---
MIN_ADX = 20           
MIN_RVOL = 1.0         
MAX_RSI_LONG = 70      
MIN_RSI_SHORT = 30     

# --- PARAMETRY FILTRACJI ---
MIN_PRICE = 5.0        
MIN_AVG_VOLUME = 50000 

# --- ŹRÓDŁA DANYCH ---
# Źródło 1: GitHub (może wygasnąć)
RUSSELL_URL = 'https://raw.githubusercontent.com/benjaminbowen/Russell-2000-Ticker-List/main/russell-2000-ticker-list.csv'
# Źródło 2: Wikipedia S&P 600 Small Cap (Zapasowe, bardzo stabilne)
WIKI_SP600_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies'

def get_tickers_robust():
    """
    Próbuje pobrać Russell 2000. W razie błędu 404, pobiera S&P 600 z Wikipedii.
    """
    # --- METODA 1: Russell 2000 z GitHub ---
    print(f"Próba 1: Pobieranie Russell 2000 z GitHub...")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(RUSSELL_URL, headers=headers)
        
        if response.status_code == 200:
            # Próba parsowania CSV
            try:
                # Niektóre listy mają nagłówki, inne nie. Sprawdzamy strukturę.
                content = response.text
                df = pd.read_csv(StringIO(content))
                
                # Szukamy kolumny z tickerami (zazwyczaj pierwsza lub 'Ticker'/'Symbol')
                if 'Ticker' in df.columns:
                    tickers = df['Ticker'].tolist()
                elif 'Symbol' in df.columns:
                    tickers = df['Symbol'].tolist()
                else:
                    tickers = df.iloc[:, 0].tolist()
                
                tickers = [str(t).strip().replace('.', '-') for t in tickers if isinstance(t, str)]
                print(f"Sukces! Pobrano {len(tickers)} tickerów z Russell 2000.")
                return tickers
            except Exception as e:
                print(f"Błąd parsowania CSV Russell: {e}")
        else:
            print(f"Błąd HTTP Russell: {response.status_code}")

    except Exception as e:
        print(f"Błąd połączenia z GitHub: {e}")

    # --- METODA 2: S&P 600 Small Cap (Wikipedia) ---
    print("⚠️ Przełączanie na źródło zapasowe: S&P 600 Small Cap (Wikipedia)...")
    try:
        tables = pd.read_html(WIKI_SP600_URL)
        df = tables[0] # Pierwsza tabela to zazwyczaj lista spółek
        tickers = df['Symbol'].tolist()
        tickers = [t.replace('.', '-') for t in tickers]
        print(f"Sukces! Pobrano {len(tickers)} tickerów z S&P 600 (Small Caps).")
        return tickers
    except Exception as e:
        print(f"Krytyczny błąd pobierania zapasowego źródła: {e}")
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
        data = yf.download(tickers_batch, period="6mo", group_by='ticker', auto_adjust=True, progress=False, threads=True)
        
        # Fix dla 1 tickera
        if len(tickers_batch) == 1: pass

        for ticker in tickers_batch:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    if len(tickers_batch) == 1: df = data.copy()
                    else: continue
                
                df.dropna(inplace=True)
                if len(df) < 60: continue

                current_price = df['Close'].iloc[-1]
                avg_vol = df['Volume'].rolling(window=20).mean().iloc[-1]

                if current_price < MIN_PRICE: continue
                if avg_vol < MIN_AVG_VOLUME: continue

                df['MA20'] = df['Close'].rolling(window=20).mean()
                df['MA50'] = df['Close'].rolling(window=50).mean()
                df['RSI'] = calculate_rsi(df['Close'])
                df['ADX'] = calculate_adx(df)
                df['VolMA20'] = df['Volume'].rolling(window=20).mean()

                today = df.iloc[-1]
                yesterday = df.iloc[-2]

                if pd.isna(today['ADX']) or pd.isna(yesterday['ADX']): continue

                # Filtr ADX
                is_adx_ok = (today['ADX'] > MIN_ADX) and (today['ADX'] > yesterday['ADX'] or today['ADX'] > 30)
                if not is_adx_ok: continue

                vol_ratio = 0
                if today['VolMA20'] > 0:
                    vol_ratio = today['Volume'] / today['VolMA20']

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

def send_email_alert(bullish, bearish, source_name):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        print(f"Symulacja. Bycze: {len(bullish)}, Niedźwiedzie: {len(bearish)}")
        if bullish: print(f"Sample Bull: {[x['ticker'] for x in bullish[:3]]}")
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"Small Caps Report ({source_name}) - {date_str}"
    
    bullish.sort(key=lambda x: x['adx'], reverse=True)
    bearish.sort(key=lambda x: x['adx'], reverse=True)
    
    top_bullish = bullish[:25]
    top_bearish = bearish[:25]

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Raport Small Caps ({source_name})</h2>
        <p>Data: <b>{date_str}</b></p>
        <div style="font-size: small; color: #555; background-color: #f4f4f4; padding: 10px;">
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
            print("E-mail wysłany.")
    except Exception as e:
        print(f"Błąd wysyłki: {e}")

def main():
    # Pobieranie tickerów z failoverem
    tickers = get_tickers_robust()
    source_name = "Russell 2000" if len(tickers) > 1000 else "S&P 600"
    
    BATCH_SIZE = 100
    total_bullish = []
    total_bearish = []
    
    print(f"Rozpoczynanie analizy {len(tickers)} spółek...")
    
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        print(f"Przetwarzanie {i} do {i + len(batch)}...")
        
        b_bull, b_bear = process_batch(batch)
        total_bullish.extend(b_bull)
        total_bearish.extend(b_bear)
        
        time.sleep(1) 
        gc.collect() 

    print(f"Koniec. Znaleziono: {len(total_bullish)} Byczych, {len(total_bearish)} Niedźwiedzich.")
    send_email_alert(total_bullish, total_bearish, source_name)

if __name__ == "__main__":
    main()
