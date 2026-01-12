import yfinance as yf
import pandas as pd
import smtplib
import os
import datetime
import sys
from email.message import EmailMessage

# Konfiguracja zmiennych środowiskowych (pobierane z GitHub Secrets)
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.environ.get('EMAIL_RECIPIENT')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465

WIKI_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

def get_sp500_tickers():
    try:
        tables = pd.read_html(WIKI_URL)
        df = tables
        tickers = df.tolist()
        tickers = [ticker.replace('.', '-') for ticker in tickers]
        print(f"Pobrano {len(tickers)} tickerów z S&P 500.")
        return tickers
    except Exception as e:
        print(f"Krytyczny błąd podczas pobierania listy tickerów: {e}")
        sys.exit(1)

def fetch_data(tickers):
    print("Rozpoczynanie pobierania danych z Yahoo Finance...")
    try:
        data = yf.download(tickers, period="6mo", group_by='ticker', auto_adjust=True, progress=False, threads=True)
        return data
    except Exception as e:
        print(f"Błąd podczas pobierania danych: {e}")
        sys.exit(1)

def calculate_signals(data, tickers):
    bullish_signals =
    bearish_signals =
    
    for ticker in tickers:
        try:
            if ticker not in data.columns.levels:
                continue

            df = data[ticker].dropna()
            
            if len(df) < 55:
                continue
                
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA50'] = df['Close'].rolling(window=50).mean()
            
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            
            if pd.isna(today['MA20']) or pd.isna(today['MA50']) or pd.isna(yesterday['MA20']) or pd.isna(yesterday['MA50']):
                continue

            if yesterday['MA20'] <= yesterday['MA50'] and today['MA20'] > today['MA50']:
                bullish_signals.append({
                    'ticker': ticker,
                    'close': today['Close'],
                    'ma20': today['MA20'],
                    'ma50': today['MA50']
                })
            
            elif yesterday['MA20'] >= yesterday['MA50'] and today['MA20'] < today['MA50']:
                bearish_signals.append({
                    'ticker': ticker,
                    'close': today['Close'],
                    'ma20': today['MA20'],
                    'ma50': today['MA50']
                })
                
        except Exception:
            continue
            
    return bullish_signals, bearish_signals

def send_email_alert(bullish, bearish):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        print("Brak danych logowania SMTP w zmiennych środowiskowych. Pomijanie wysyłki.")
        print(f"Znaleziono bycze: {len(bullish)}")
        print(f"Znaleziono niedźwiedzie: {len(bearish)}")
        return

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    subject = f"Raport S&P 500 MA20/MA50 - {date_str}"
    
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Raport Dzienny S&P 500</h2>
        <p>Data analizy: <b>{date_str}</b></p>
        <hr>
    """
    
    if not bullish and not bearish:
        html_content += "<p>Brak sygnałów przecięcia średnich podczas dzisiejszej sesji.</p>"
    
    if bullish:
        html_content += "<h3 style='color: green;'>🚀 Golden Cross (MA20 > MA50)</h3><ul>"
        for item in bullish:
            html_content += f"<li><b>{item['ticker']}</b>: Cena ${item['close']:.2f} (MA20: {item['ma20']:.2f}, MA50: {item['ma50']:.2f})</li>"
        html_content += "</ul>"
        
    if bearish:
        html_content += "<h3 style='color: red;'>📉 Death Cross (MA20 < MA50)</h3><ul>"
        for item in bearish:
            html_content += f"<li><b>{item['ticker']}</b>: Cena ${item['close']:.2f} (MA20: {item['ma20']:.2f}, MA50: {item['ma50']:.2f})</li>"
        html_content += "</ul>"

    html_content += """
        <hr>
        <p style="font-size: small; color: gray;">Wygenerowano automatycznie przez GitHub Actions.</p>
      </body>
    </html>
    """

    msg = EmailMessage()
    msg = subject
    msg['From'] = EMAIL_SENDER
    msg = EMAIL_RECIPIENT
    msg.set_content("Twoja skrzynka nie obsługuje HTML.")
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.send_message(msg)
            print("E-mail został wysłany pomyślnie.")
    except Exception as e:
        print(f"Błąd wysyłki e-maila: {e}")

if __name__ == "__main__":
    main()
