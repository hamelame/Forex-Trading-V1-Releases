DEFAULT_UNIVERSE = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD', 'EURGBP', 'EURJPY', 'EURCHF', 'EURCAD', 'EURAUD', 'EURNZD', 'GBPJPY', 'GBPCHF', 'GBPCAD', 'GBPAUD', 'GBPNZD', 'AUDJPY', 'AUDCAD', 'AUDCHF', 'AUDNZD', 'NZDJPY', 'NZDCAD', 'NZDCHF', 'CADJPY', 'CADCHF', 'CHFJPY', 'USDNOK', 'EURNOK', 'GBPNOK', 'AUDNOK', 'USDSEK', 'EURSEK', 'GBPSEK', 'USDSGD', 'EURSGD', 'GBPSGD', 'AUDSGD', 'NZDSGD', 'SGDJPY', 'CHFSGD', 'USDCNH', 'USDHKD', 'USDZAR', 'USDMXN', 'USDPLN', 'USDCZK', 'USDHUF', 'USDTRY', 'EURZAR', 'GBPZAR', 'EURPLN', 'EURCZK', 'EURHUF', 'EURTRY', 'CADNOK', 'CADSEK', 'CHFNOK', 'CHFSEK', 'NOKSEK', 'EURMXN', 'GBPMXN', 'EURCNH', 'GBPCNH', 'AUDCNH', 'NZDCNH', 'CADSGD', 'NOKJPY', 'SEKJPY', 'MXNJPY', 'ZARJPY', 'XAUUSD', 'XAGUSD', 'XPTUSD', 'XPDUSD', 'XAUEUR', 'XAUGBP', 'XAGEUR', 'XAGGBP', 'WTIUSD', 'BRENTUSD', 'NATGASUSD', 'GASOILUSD', 'HEATOILUSD', 'RBOBUSD', 'COALUSD', 'URANIUMUSD', 'US500', 'US100', 'US30', 'US2000', 'UK100', 'DE40', 'FR40', 'EU50', 'ES35', 'IT40', 'CH20', 'NL25', 'JP225', 'HK50', 'CN50', 'AUS200', 'SG30', 'IN50', 'KR200', 'CA60', 'BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'BNBUSD', 'ADAUSD', 'DOGEUSD', 'AVAXUSD', 'LINKUSD', 'DOTUSD', 'LTCUSD', 'BCHUSD', 'TRXUSD', 'XLMUSD', 'ATOMUSD', 'UNIUSD', 'AAVEUSD', 'NEARUSD', 'SUIUSD', 'TONUSD', 'HBARUSD', 'ICPUSD', 'FILUSD', 'ETCUSD', 'ALGOUSD', 'VETUSD', 'APTUSD', 'ARBUSD', 'OPUSD', 'PEPEUSD', 'SHIBUSD', 'XMRUSD']

CURRENCY_NAMES = {'USD': 'US Dollar', 'EUR': 'Euro', 'GBP': 'British Pound', 'JPY': 'Japanese Yen', 'CHF': 'Swiss Franc', 'CAD': 'Canadian Dollar', 'AUD': 'Australian Dollar', 'NZD': 'New Zealand Dollar', 'NOK': 'Norwegian Krone', 'SEK': 'Swedish Krona', 'SGD': 'Singapore Dollar', 'CNH': 'Chinese Yuan (Offshore)', 'HKD': 'Hong Kong Dollar', 'ZAR': 'South African Rand', 'MXN': 'Mexican Peso', 'PLN': 'Polish Zloty', 'CZK': 'Czech Koruna', 'HUF': 'Hungarian Forint', 'TRY': 'Turkish Lira'}

MAJORS = {'USDJPY', 'USDCAD', 'NZDUSD', 'GBPUSD', 'USDCHF', 'AUDUSD', 'EURUSD'}
NORDIC = {'NOKJPY', 'CHFSEK', 'EURSEK', 'EURNOK', 'GBPSEK', 'CADSEK', 'SEKJPY', 'NOKSEK', 'GBPNOK', 'AUDNOK', 'USDSEK', 'CADNOK', 'CHFNOK', 'USDNOK'}
ASIA = {'GBPSGD', 'GBPCNH', 'CADSGD', 'EURSGD', 'NZDSGD', 'NZDCNH', 'AUDSGD', 'USDSGD', 'CHFSGD', 'EURCNH', 'AUDCNH', 'USDCNH', 'SGDJPY', 'USDHKD'}
EMERGING = {'EURZAR', 'USDZAR', 'USDMXN', 'EURHUF', 'EURMXN', 'GBPMXN', 'ZARJPY', 'USDCZK', 'USDTRY', 'USDHUF', 'EURCZK', 'GBPZAR', 'EURPLN', 'USDPLN', 'EURTRY', 'MXNJPY'}
FX_SYMBOLS = {'USDJPY', 'NOKJPY', 'USDZAR', 'CADJPY', 'NZDUSD', 'USDMXN', 'CADSGD', 'USDTRY', 'EURCZK', 'CADNOK', 'USDCNH', 'CADCHF', 'SGDJPY', 'USDHKD', 'AUDJPY', 'EURCAD', 'EURTRY', 'GBPSGD', 'EURZAR', 'EURSEK', 'GBPCNH', 'EURCHF', 'NZDSGD', 'USDSEK', 'EURUSD', 'EURAUD', 'GBPMXN', 'NZDCHF', 'USDSGD', 'CHFSGD', 'EURCNH', 'GBPZAR', 'USDNOK', 'GBPUSD', 'GBPAUD', 'USDPLN', 'SEKJPY', 'MXNJPY', 'AUDNZD', 'NZDCAD', 'GBPNZD', 'CHFSEK', 'AUDCAD', 'CHFJPY', 'EURSGD', 'NOKSEK', 'USDCHF', 'AUDNOK', 'USDHUF', 'AUDSGD', 'AUDCNH', 'EURPLN', 'NZDJPY', 'USDCAD', 'EURNZD', 'EURNOK', 'GBPSEK', 'EURHUF', 'EURGBP', 'CADSEK', 'EURMXN', 'AUDUSD', 'GBPNOK', 'GBPCHF', 'USDCZK', 'EURJPY', 'GBPJPY', 'NZDCNH', 'AUDCHF', 'ZARJPY', 'CHFNOK', 'GBPCAD'}
NON_FX = {'XAUUSD': ('Gold / US Dollar', 'Precious Metal', 2350.0, 0.1, 0.7, 1.1, 10.0), 'XAGUSD': ('Silver / US Dollar', 'Precious Metal', 28.5, 0.01, 0.8, 1.25, 10.0), 'XPTUSD': ('Platinum / US Dollar', 'Precious Metal', 990.0, 0.1, 0.9, 1.2, 10.0), 'XPDUSD': ('Palladium / US Dollar', 'Precious Metal', 980.0, 0.1, 1.0, 1.35, 10.0), 'XAUEUR': ('Gold / Euro', 'Precious Metal', 2150.0, 0.1, 0.8, 1.1, 10.0), 'XAUGBP': ('Gold / British Pound', 'Precious Metal', 1850.0, 0.1, 0.8, 1.1, 10.0), 'XAGEUR': ('Silver / Euro', 'Precious Metal', 26.0, 0.01, 0.9, 1.25, 10.0), 'XAGGBP': ('Silver / British Pound', 'Precious Metal', 22.3, 0.01, 0.9, 1.25, 10.0), 'WTIUSD': ('WTI Crude Oil', 'Energy', 78.0, 0.01, 0.9, 1.35, 10.0), 'BRENTUSD': ('Brent Crude Oil', 'Energy', 82.0, 0.01, 0.9, 1.3, 10.0), 'NATGASUSD': ('Natural Gas', 'Energy', 2.8, 0.001, 1.0, 1.55, 10.0), 'GASOILUSD': ('London Gas Oil', 'Energy', 760.0, 0.1, 1.0, 1.3, 10.0), 'HEATOILUSD': ('Heating Oil', 'Energy', 2.45, 0.001, 1.0, 1.45, 10.0), 'RBOBUSD': ('RBOB Gasoline', 'Energy', 2.35, 0.001, 1.0, 1.45, 10.0), 'COALUSD': ('Thermal Coal', 'Energy', 135.0, 0.1, 1.1, 1.25, 10.0), 'URANIUMUSD': ('Uranium', 'Energy', 72.0, 0.1, 1.2, 1.3, 10.0), 'US500': ('S&P 500', 'US Index', 6300.0, 0.1, 0.7, 0.95, 5.0), 'US100': ('Nasdaq 100', 'US Index', 23000.0, 0.1, 0.8, 1.15, 5.0), 'US30': ('Dow Jones 30', 'US Index', 45000.0, 1.0, 0.8, 0.9, 1.0), 'US2000': ('Russell 2000', 'US Index', 2250.0, 0.1, 0.9, 1.2, 5.0), 'UK100': ('FTSE 100', 'European Index', 8800.0, 0.1, 0.8, 0.9, 5.0), 'DE40': ('DAX 40', 'European Index', 24000.0, 0.1, 0.8, 1.05, 5.0), 'FR40': ('CAC 40', 'European Index', 7800.0, 0.1, 0.8, 0.95, 5.0), 'EU50': ('Euro Stoxx 50', 'European Index', 5400.0, 0.1, 0.8, 0.95, 5.0), 'ES35': ('IBEX 35', 'European Index', 14200.0, 0.1, 0.9, 1.0, 5.0), 'IT40': ('FTSE MIB 40', 'European Index', 41000.0, 1.0, 0.9, 1.0, 1.0), 'CH20': ('Swiss Market Index', 'European Index', 12200.0, 0.1, 0.8, 0.85, 5.0), 'NL25': ('AEX 25', 'European Index', 920.0, 0.01, 0.8, 0.9, 10.0), 'JP225': ('Nikkei 225', 'Asia-Pacific Index', 43000.0, 1.0, 0.9, 1.05, 1.0), 'HK50': ('Hang Seng 50', 'Asia-Pacific Index', 25000.0, 1.0, 1.0, 1.3, 1.0), 'CN50': ('China A50', 'Asia-Pacific Index', 14000.0, 1.0, 1.1, 1.25, 1.0), 'AUS200': ('ASX 200', 'Asia-Pacific Index', 8900.0, 0.1, 0.9, 0.95, 5.0), 'SG30': ('Singapore 30', 'Asia-Pacific Index', 410.0, 0.01, 0.9, 0.85, 10.0), 'IN50': ('Nifty 50', 'Asia-Pacific Index', 24800.0, 1.0, 1.0, 1.05, 1.0), 'KR200': ('KOSPI 200', 'Asia-Pacific Index', 350.0, 0.01, 1.0, 1.0, 10.0), 'CA60': ('Canada 60', 'North America Index', 1400.0, 0.1, 0.9, 0.9, 5.0), 'BTCUSD': ('Bitcoin / US Dollar', 'Crypto', 118000, 0.1, 1.0, 1.65, 5.0), 'ETHUSD': ('Ethereum / US Dollar', 'Crypto', 4600, 0.1, 1.0, 1.65, 5.0), 'SOLUSD': ('Solana / US Dollar', 'Crypto', 195, 0.01, 1.0, 1.65, 5.0), 'XRPUSD': ('XRP / US Dollar', 'Crypto', 3.0, 0.001, 1.0, 1.65, 5.0), 'BNBUSD': ('BNB / US Dollar', 'Crypto', 850, 0.01, 1.0, 1.65, 5.0), 'ADAUSD': ('Cardano / US Dollar', 'Crypto', 0.95, 1e-05, 1.0, 1.65, 5.0), 'DOGEUSD': ('Dogecoin / US Dollar', 'Crypto', 0.23, 1e-05, 1.0, 1.65, 5.0), 'AVAXUSD': ('Avalanche / US Dollar', 'Crypto', 26, 0.001, 1.0, 1.65, 5.0), 'LINKUSD': ('Chainlink / US Dollar', 'Crypto', 24, 0.001, 1.0, 1.65, 5.0), 'DOTUSD': ('Polkadot / US Dollar', 'Crypto', 4.2, 0.001, 1.0, 1.65, 5.0), 'LTCUSD': ('Litecoin / US Dollar', 'Crypto', 125, 0.01, 1.0, 1.65, 5.0), 'BCHUSD': ('Bitcoin Cash / US Dollar', 'Crypto', 590, 0.01, 1.0, 1.65, 5.0), 'TRXUSD': ('TRON / US Dollar', 'Crypto', 0.34, 1e-05, 1.0, 1.65, 5.0), 'XLMUSD': ('Stellar / US Dollar', 'Crypto', 0.43, 1e-05, 1.0, 1.65, 5.0), 'ATOMUSD': ('Cosmos / US Dollar', 'Crypto', 4.8, 0.001, 1.0, 1.65, 5.0), 'UNIUSD': ('Uniswap / US Dollar', 'Crypto', 10.5, 0.001, 1.0, 1.65, 5.0), 'AAVEUSD': ('Aave / US Dollar', 'Crypto', 290, 0.01, 1.0, 1.65, 5.0), 'NEARUSD': ('NEAR Protocol / US Dollar', 'Crypto', 2.8, 0.001, 1.0, 1.65, 5.0), 'SUIUSD': ('Sui / US Dollar', 'Crypto', 3.8, 0.001, 1.0, 1.65, 5.0), 'TONUSD': ('Toncoin / US Dollar', 'Crypto', 3.5, 0.001, 1.0, 1.65, 5.0), 'HBARUSD': ('Hedera / US Dollar', 'Crypto', 0.24, 1e-05, 1.0, 1.65, 5.0), 'ICPUSD': ('Internet Computer / US Dollar', 'Crypto', 5.6, 0.001, 1.0, 1.65, 5.0), 'FILUSD': ('Filecoin / US Dollar', 'Crypto', 2.6, 0.001, 1.0, 1.65, 5.0), 'ETCUSD': ('Ethereum Classic / US Dollar', 'Crypto', 22, 0.001, 1.0, 1.65, 5.0), 'ALGOUSD': ('Algorand / US Dollar', 'Crypto', 0.27, 1e-05, 1.0, 1.65, 5.0), 'VETUSD': ('VeChain / US Dollar', 'Crypto', 0.026, 1e-05, 1.0, 1.65, 5.0), 'APTUSD': ('Aptos / US Dollar', 'Crypto', 4.8, 0.001, 1.0, 1.65, 5.0), 'ARBUSD': ('Arbitrum / US Dollar', 'Crypto', 0.48, 1e-05, 1.0, 1.65, 5.0), 'OPUSD': ('Optimism / US Dollar', 'Crypto', 0.72, 1e-05, 1.0, 1.65, 5.0), 'PEPEUSD': ('Pepe / US Dollar', 'Crypto', 1.2e-05, 1e-08, 1.0, 1.65, 5.0), 'SHIBUSD': ('Shiba Inu / US Dollar', 'Crypto', 1.4e-05, 1e-08, 1.0, 1.65, 5.0), 'XMRUSD': ('Monero / US Dollar', 'Crypto', 270, 0.01, 1.0, 1.65, 5.0)}
BASE_FX = {'EURUSD': 1.165, 'GBPUSD': 1.345, 'USDJPY': 147.2, 'AUDUSD': 0.651, 'USDCAD': 1.382, 'USDCHF': 0.806, 'NZDUSD': 0.59, 'EURGBP': 0.866, 'EURJPY': 171.5, 'GBPJPY': 198.1, 'EURCHF': 0.939, 'EURCAD': 1.61, 'EURAUD': 1.79, 'EURNZD': 1.975, 'EURNOK': 11.8, 'EURSEK': 11.1, 'GBPCHF': 1.083, 'GBPCAD': 1.858, 'GBPAUD': 2.065, 'GBPNZD': 2.278, 'AUDJPY': 95.85, 'AUDCAD': 0.9, 'AUDCHF': 0.525, 'AUDNZD': 1.104, 'NZDJPY': 86.85, 'NZDCAD': 0.815, 'NZDCHF': 0.476, 'CADJPY': 106.5, 'CADCHF': 0.583, 'CHFJPY': 182.7, 'USDNOK': 10.13, 'USDSEK': 9.53, 'USDSGD': 1.285, 'EURSGD': 1.497, 'GBPSGD': 1.729, 'AUDSGD': 0.837, 'NZDSGD': 0.758, 'SGDJPY': 114.55, 'CHFSGD': 1.594, 'USDCNH': 7.18, 'USDHKD': 7.82, 'USDZAR': 17.75, 'USDMXN': 18.65, 'USDPLN': 3.78, 'USDCZK': 21.45, 'USDHUF': 340.0, 'USDTRY': 40.7, 'EURZAR': 20.68, 'GBPZAR': 23.86, 'EURPLN': 4.4, 'EURCZK': 24.98, 'EURHUF': 396.0, 'EURTRY': 47.4, 'GBPNOK': 13.62, 'GBPSEK': 12.82, 'AUDNOK': 6.6, 'CADNOK': 7.33, 'CADSEK': 6.9, 'CHFNOK': 12.55, 'CHFSEK': 11.82, 'NOKSEK': 0.94, 'EURMXN': 21.7, 'GBPMXN': 25.1, 'EURCNH': 8.36, 'GBPCNH': 9.66, 'AUDCNH': 4.67, 'NZDCNH': 4.24, 'CADSGD': 0.93, 'NOKJPY': 14.5, 'SEKJPY': 15.4, 'MXNJPY': 7.9, 'ZARJPY': 8.3}

def forex_pip_size(symbol):
    """Synthetic standard pip size for FX.

    JPY and HUF are handled as 2-decimal pip markets in this research feed.
    Other FX quotes use the conventional 0.0001 pip.
    """
    s=symbol.upper()
    if s not in FX_SYMBOLS:
        return float(NON_FX.get(s,("", "", 0, 0.0001, 0, 0, 0))[3])
    quote=s[3:6]
    return 0.01 if quote in {"JPY","HUF"} else 0.0001


def quote_currency_to_usd(quote, pair_price=None, symbol=None):
    """Approximate one unit of quote currency in USD using the synthetic FX universe."""
    q=str(quote).upper()
    if q=="USD":
        return 1.0

    s=(symbol or "").upper()
    # If the current instrument is USD/QUOTE, its live price is the best conversion.
    if pair_price and s.startswith("USD") and s[3:6]==q:
        return 1.0/max(float(pair_price),1e-12)

    direct=f"{q}USD"
    inverse=f"USD{q}"
    if direct in BASE_FX:
        return float(BASE_FX[direct])
    if inverse in BASE_FX:
        return 1.0/max(float(BASE_FX[inverse]),1e-12)
    # Conservative fallback for an unknown quote currency.
    return 1.0


def forex_pip_value_usd(symbol, pair_price=None, contract_size=100000.0):
    """USD value of one standard pip for one FX lot."""
    s=symbol.upper()
    if s not in FX_SYMBOLS:
        return float(instrument_meta(s).get("point_value",10.0))
    quote=s[3:6]
    pip=forex_pip_size(s)
    quote_value=float(contract_size)*pip
    return quote_value*quote_currency_to_usd(quote,pair_price,s)


def instrument_meta(symbol):
    s=symbol.upper()
    if s in FX_SYMBOLS:
        b,q=s[:3],s[3:6]
        if s in MAJORS: category='Forex Major'
        elif s in NORDIC: category='Forex / Nordic'
        elif s in ASIA: category='Forex / Asia'
        elif s in EMERGING: category='Forex / Emerging'
        else: category='Forex Cross'
        pip=forex_pip_size(s)
        return {'symbol':s,'name':f"{CURRENCY_NAMES.get(b,b)} / {CURRENCY_NAMES.get(q,q)}",'category':category,'asset_class':'FOREX','short':f"{b} / {q}",'base_price':BASE_FX.get(s,1.0),'tick_size':pip,'spread_base':0.85,'volatility':1.0,'point_value':forex_pip_value_usd(s,BASE_FX.get(s,1.0)),'contract_size':100000.0,'min_lot':0.01,'lot_step':0.01}
    if s in NON_FX:
        name,category,base,tick,spread,vol,pv=NON_FX[s]
        asset='METALS' if 'Metal' in category else ('ENERGY' if category=='Energy' else ('INDICES' if 'Index' in category else 'CRYPTO'))
        if asset=='CRYPTO':
            tick = 1.0 if base>=10000 else (0.1 if base>=1000 else (0.01 if base>=100 else (0.001 if base>=1 else (0.00001 if base>=0.01 else 0.00000001))))
        return {'symbol':s,'name':name,'category':category,'asset_class':asset,'short':name,'base_price':base,'tick_size':tick,'spread_base':spread,'volatility':vol,'point_value':pv}
    return {'symbol':s,'name':s,'category':'Synthetic Instrument','asset_class':'OTHER','short':s,'base_price':1.0,'tick_size':0.0001,'spread_base':1.0,'volatility':1.0,'point_value':10.0}

def instrument_spec(symbol):
    return instrument_meta(symbol)

def is_forex(symbol):
    return instrument_meta(symbol)['asset_class']=='FOREX'

def display_name(symbol):
    m=instrument_meta(symbol)
    return f"{m['symbol']}  ·  {m['name']}  ·  {m['category']}"

def universe_counts(symbols):
    out={'FOREX':0,'METALS':0,'ENERGY':0,'INDICES':0,'CRYPTO':0,'OTHER':0}
    for s in symbols:
        a=instrument_meta(s)['asset_class']; out[a]=out.get(a,0)+1
    return out


def underlying_key(symbol):
    """Canonical underlying identity used for duplicate-exposure protection."""
    s=symbol.upper()
    meta=instrument_meta(s)
    asset=meta["asset_class"]
    if asset=="METALS":
        return f"METAL:{s[:3]}"
    if asset=="CRYPTO" and s.endswith("USD"):
        return f"CRYPTO:{s[:-3]}"
    if asset=="ENERGY":
        # WTI and Brent are related but not identical; keep distinct.
        return f"ENERGY:{s}"
    if asset=="INDICES":
        return f"INDEX:{s}"
    if asset=="FOREX":
        return f"FX:{s}"
    return f"{asset}:{s}"

def exposure_tokens(symbol):
    """Shared exposure tokens for diversification/correlation controls."""
    s=symbol.upper()
    meta=instrument_meta(s)
    asset=meta["asset_class"]
    if asset=="FOREX" and len(s)>=6:
        return {f"CCY:{s[:3]}",f"CCY:{s[3:6]}"}
    if asset=="METALS":
        return {underlying_key(s)}
    if asset=="CRYPTO":
        return {underlying_key(s),"CRYPTO:BETA"}
    if asset=="INDICES":
        cat=meta.get("category","")
        region="US" if cat=="US Index" else "EUROPE" if "European" in cat else "APAC" if "Asia-Pacific" in cat else "OTHER"
        return {underlying_key(s),f"INDEX_REGION:{region}"}
    return {underlying_key(s)}
