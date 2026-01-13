import yfinance as yf
import pandas as pd
import numpy as np  # Potrzebne do obliczeń ADX
import smtplib
import os
import datetime
import sys
import requests
from io import StringIO
from email.message import EmailMessage

# Konfiguracja zmiennych
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465
WIKI_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

# --- PARAMETRY STRATEGII ---
MIN_RVOL = 1.1         # Wolumen > 110% średniej (tylko jako potwierdzenie siły)
# Kryteria ADX (Filtr Trendu)
MIN_ADX = 25           # Minimalna wartość siły trendu
# Kryteria RSI (Filtr Wejścia)
MAX_RSI_LONG = 65      # Nie kupuj, jeśli RSI > 65
MIN_RSI_SHORT = 35     # Nie sprzedawaj, jeśli RSI < 35

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
    """
    Oblicza wskaźnik ADX (Average Directional Index).
    Wymaga kolumn: High, Low, Close.
    """
    # Obliczanie Directional Movement
    plus_dm = df['High'].diff()
    minus_dm = df['Low'].diff()
    
    # Warunki dla +DM i -DM
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    
    # True Range (TR)
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Wygładzanie (Wilder's Smoothing przy użyciu ewm alpha=1/period)
    atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr)
    
    # DX i ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    
    return adx

def calculate_signals(data, tickers):
    bullish_signals = []
    bearish_signals = []
    
    print("Analiza wskaźników (MA, RSI, ADX)...")
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels[0]: continue

            df = data[ticker].dropna()
            if len(df) < 60: continue # Potrzebujemy więcej danych dla ADX

            # 1. Średnie kroczące
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            
            # 2. RSI
            df['RSI'] = calculate_rsi(df['Close'])
            
            # 3. ADX (Wymaga High/Low/Close)
            df['ADX'] = calculate_adx(df)
            
            # 4. Wolumen (dla dodatkowego potwierdzenia)
            df['VolMA20'] = df['Volume'].rolling(window=20).mean()

            today = df.iloc[-1]
            yesterday = df.iloc[-2]

            # Sprawdzenie braków danych
            if (pd.isna(today['MA20']) or pd.isna(today['MA50']) or 
                pd.isna(today['RSI']) or pd.isna(today['ADX']) or 
                pd.isna(yesterday['ADX'])):
                continue

            # --- FILTR ADX (KRYTYCZNY) ---
            # Zasada: ADX musi rosnąć I wynosić minimum 25.
            # Jeśli nie spełnia -> odrzucamy spółkę natychmiast.
            if today['ADX'] < MIN_ADX or today['ADX'] <= yesterday['ADX']:
                continue 

            vol_ratio = 0
            if today['VolMA20'] > 0:
                vol_ratio = today['Volume'] / today['VolMA20']
            
            # --- LOGIKA SYGNAŁÓW ---

            # GOLDEN CROSS (MA20 > MA50)
            if (yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']):
                # Filtr RSI Swing: Nie wchodzimy, jeśli już jest drogo (>65)
                if today['RSI'] <= MAX_RSI_LONG:
                    bullish_signals.append({
                        'ticker': ticker,
                        'close': today['Close'],
                        'ma20': today['MA20'],
                        'ma50': today['MA50'],
                        'rsi': today['RSI'],
                        'adx': today['ADX'],
                        'vol_ratio': vol_ratio
                    })

            # DEATH CROSS (MA20 < MA50)
            elif (yesterday['MA20'] >= yesterday['MA50'] and today['MA20'] < today['MA50']):
                # Filtr RSI Swing: Nie wchodzimy, jeśli już jest tanio (<35)
                if today['RSI'] >= MIN_RSI_SHORT:
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
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"Raport S&P 500 (ADX Filtered) - {date_str}"
    
    # Sortujemy po sile trendu (ADX), bo to teraz kluczowy wskaźnik
    bullish.sort(key=lambda x: x['adx'], reverse=True)
    bearish.sort(key=lambda x: x['adx'], reverse=True)

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Raport Strategiczny S&P 500</h2>
        <p>Data: <b>{date_str}</b></p>
        <div style="background-color: #f0f0f0; padding: 10px; border-radius: 5px; font-size: small;">
            <b>Zastosowane filtry:</b><br>
            1. Przecięcie średnich MA20/MA50.<br>
            2. <b>ADX (Trend)</b>: > {MIN_ADX} i rosnący (odrzuca trend boczny).<br>
            3. <b>RSI (Wejście)</b>: Long max {MAX_RSI_LONG}, Short min {MIN_RSI_SHORT}.
        </div>
        <hr>
    """
    
    if not bullish and not bearish:
        html_content += "<p>Brak sygnałów spełniających rygorystyczne kryteria ADX/RSI.</p>"
    
    if bullish:
        html_content += "<h3 style='color: green;'>🚀 Potwierdzone Sygnały Kupna (Golden Cross)</h3><ul>"
        for item in bullish:
            # Pogrubienie jeśli duży wolumen
            vol_str = f"<b>{item['vol_ratio']:.1f}x</b>" if item['vol_ratio'] > MIN_RVOL else f"{item['vol_ratio']:.1f}x"
            
            html_content += f"""
            <li style="margin-bottom: 8px;">
                <b>{item['ticker']}</b> (${item['close']:.2f})<br>
                <span style="font-size: small; color: #333;">
                   Trend: <b>ADX {item['adx']:.1f} ↗</b> | RSI: {item['rsi']:.1f} | Vol: {vol_str}
                </span>
            </li>"""
        html_content += "</ul>"
        
    if bearish:
        html_content += "<h3 style='color: red;'>📉 Potwierdzone Sygnały Sprzedaży (Death Cross)</h3><ul>"
        for item in bearish:
            vol_str = f"<b>{item['vol_ratio']:.1f}x</b>" if item['vol_ratio'] > MIN_RVOL else f"{item['vol_ratio']:.1f}x"

            html_content += f"""
            <li style="margin-bottom: 8px;">
                <b>{item['ticker']}</b> (${item['close']:.2f})<br>
                <span style="font-size: small; color: #333;">
                   Trend: <b>ADX {item['adx']:.1f} ↗</b> | RSI: {item['rsi']:.1f} | Vol: {vol_str}
                </span>
            </li>"""
        html_content += "</ul>"

    html_content += """
        <hr>
        <p style="font-size: small; color: gray;">
           Sygnały są posortowane od najsilniejszego trendu (najwyższy ADX).
        </p>
      </body>
    </html>
    """

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
    print(f"Wynik po filtracji: {len(bullish)} Byczych, {len(bearish)} Niedźwiedzich.")
    send_email_alert(bullish, bearish)

if __name__ == "__main__":
    main()
