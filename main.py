from indicators import analyze_multiple_tickers 
from news_feed import fetch_latest_news
from trading_agent import previsione_trading_agent
from utils import check_stop_loss
from whalealert import format_whale_alerts_to_string
from sentiment import get_sentiment
from forecaster import get_crypto_forecasts
from hyperliquid_trader import HyperLiquidTrader

import os
import json
import db_utils
from dotenv import load_dotenv
import psycopg2  # <-- aggiunto per la migrazione DB

# Carica eventuali variabili da .env (solo locale).
# Su Railway useremo le variabili d'ambiente che hai messo nella tab Variables.
load_dotenv()

# Collegamento ad Hyperliquid
TESTNET = False  # True = testnet, False = mainnet (OCCHIO!)
VERBOSE = True   # stampa informazioni extra

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

# Debug leggero: stampa solo se ESISTONO (True/False), non i valori.
print(f"[DEBUG] PRIVATE_KEY settata: {bool(PRIVATE_KEY)}, WALLET_ADDRESS settata: {bool(WALLET_ADDRESS)}")

if not PRIVATE_KEY or not WALLET_ADDRESS:
    raise RuntimeError(
        "PRIVATE_KEY o WALLET_ADDRESS non trovate nelle variabili d'ambiente. "
        "Controlla la sezione Variables del servizio su Railway."
    )

def ensure_stop_loss_column():
    """
    Migrazione semplice: assicura che la colonna stop_loss_percent
    esista nella tabella bot_operations. Se non esiste, la crea.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[DB] DATABASE_URL non impostata, salto migrazione stop_loss_percent.")
        return

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            ALTER TABLE IF EXISTS bot_operations
            ADD COLUMN IF NOT EXISTS stop_loss_percent DOUBLE PRECISION;
            """
        )
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] Colonna stop_loss_percent ok (creata o già esistente).")
    except Exception as e:
        print(f"[DB] Errore migrazione stop_loss_percent: {e}")

# Inizializza (o verifica) lo schema del database: crea le tabelle se non esistono.
print("[DB] Inizializzo (o verifico) lo schema del database...")
db_utils.init_db()
print("[DB] Schema del database pronto.")

# Assicuro la presenza della colonna stop_loss_percent
ensure_stop_loss_column()

# Valori di default per evitare NameError nel blocco di except
system_prompt = ""
tickers = ['BTC', 'ETH', 'SOL']
indicators_json = {}
news_txt = ""
sentiment_json = {}
forecasts_json = {}
account_status = {}

try:
    bot = HyperLiquidTrader(
        secret_key=PRIVATE_KEY,
        account_address=WALLET_ADDRESS,
        testnet=TESTNET
    )

    # Calcolo delle informazioni in input per Ticker
    tickers = ['BTC', 'ETH', 'SOL']
    indicators_txt, indicators_json  = analyze_multiple_tickers(tickers)
    news_txt = fetch_latest_news()
    # whale_alerts_txt = format_whale_alerts_to_string()
    sentiment_txt, sentiment_json  = get_sentiment()
    forecasts_txt, forecasts_json = get_crypto_forecasts()

    msg_info = f"""<indicatori>\n{indicators_txt}\n</indicatori>\n\n
    <news>\n{news_txt}</news>\n\n
    <sentiment>\n{sentiment_txt}\n</sentiment>\n\n
    <forecast>\n{forecasts_txt}\n</forecast>\n\n"""

    account_status = bot.get_account_status()

    stop_losses = check_stop_loss(account_status)

    portfolio_data = f"{json.dumps(account_status)}\n Stop Loss attivati 15 min fa: {stop_losses}"

    # Scrivo su DB come sta
    snapshot_id = db_utils.log_account_status(account_status)
    print(f"[db_utils] Snapshot account inserito con id={snapshot_id}")

    # Creating System prompt
    with open('system_prompt.txt', 'r') as f:
        system_prompt = f.read()
    system_prompt = system_prompt.format(portfolio_data, msg_info)
        
    print("L'agente sta decidendo la sua azione!")
    out = previsione_trading_agent(system_prompt)
    bot.execute_signal(out)

    op_id = db_utils.log_bot_operation(
        out,
        system_prompt=system_prompt,
        indicators=indicators_json,
        news_text=news_txt,
        sentiment=sentiment_json,
        forecasts=forecasts_json
    )
    print(f"[db_utils] Operazione inserita con id={op_id}")

    account_status = bot.get_account_status()
    with open('account_status_old.json', 'w') as f:
        json.dump(account_status['open_positions'], f, indent=4)
    snapshot_id = db_utils.log_account_status(account_status)
    print(f"[db_utils] Snapshot finale inserito con id={snapshot_id}")

except Exception as e:
    # Qui ora tutte le variabili esistono (anche se vuote), quindi niente NameError
    db_utils.log_error(
        e,
        context={
            "prompt": system_prompt,
            "tickers": tickers,
            "indicators": indicators_json,
            "news": news_txt,
            "sentiment": sentiment_json,
            "forecasts": forecasts_json,
            "balance": account_status,
        },
        source="trading_agent"
    )
    print(f"An error occurred: {e}")
