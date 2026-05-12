import asyncio
import aiohttp
import json
import time
import sys
import pandas as pd
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm  # Importação específica para asyncio

# --- Configurações ---
BASE_URL = "https://www.farmaponte.com.br/saude/medicamentos/?p={}"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
}
CONCURRENCY_LIMIT = 40 

# --- Funções Auxiliares de Extração (Mantidas conforme seu original) ---

def limpar_preco(texto):
    if not texto: return "0"
    return texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()

def limpar_porcentagem(texto):
    if not texto: return "0"
    return texto.replace("%", "").replace("-", "").replace(" ", "").strip()

def achar_EAN_gtin(soup):
    try:
        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            if script.string:
                dados_json = json.loads(script.string)
                itens = dados_json if isinstance(dados_json, list) else [dados_json]
                for item in itens:
                    if item.get('@type') == 'Product' and 'gtin13' in item:
                        return item['gtin13']
        return "NA"
    except: return "NA"

def achar_nome(soup):
    try:
        meta = soup.select_one("meta[property='og:title']")
        return meta["content"].strip() if meta else "NA"
    except: return "NA"

# ... (As outras funções achar_marca, achar_preco, etc., permanecem iguais)
def achar_marca(soup):
    try: return soup.select_one(".title_marca").get_text(strip=True)
    except: return "NA"

def achar_preco_antes_desconto(soup):
    try: 
        texto = soup.select_one(".unit-price").get_text(strip=True)
        return limpar_preco(texto)
    except: return "0"

def achar_desconto(soup):
    try: 
        texto = soup.select_one(".discount").get_text(strip=True)
        return limpar_porcentagem(texto)
    except: return "NA"

def achar_preco(soup):
    try: 
        texto = soup.select_one("p.sale-price:not(.sale-price-pix)").get_text(strip=True)
        return limpar_preco(texto)
    except: return "0"

def achar_pix(soup):
    try: 
        texto = soup.select_one(".sale-price-pix").get_text(strip=True)
        return limpar_preco(texto)
    except: return "0"

def achar_cashback(soup):
    try: 
        texto = soup.select_one("strong.loyalty_price").get_text(strip=True)
        return limpar_preco(texto)
    except: return "NA"

def achar_apenas_pix(soup):
    try:
        selo_pix = soup.select_one(".seal-desconto-pix")
        return "Sim" if selo_pix and "PIX" in selo_pix.get_text(strip=True).upper() else "Não"
    except: return "Não"

def achar_promo_volume(soup):
    try:
        for b in soup.find_all('b'):
            texto = b.get_text(strip=True)
            if "A PARTIR DE" in texto.upper(): return " ".join(texto.split()) 
        return "NA"
    except: return "NA"

# --- Processamento Assíncrono ---

async def processar_produto(session, url, semaphore):
    async with semaphore:
        try:
            async with session.get(url, timeout=25) as response:
                if response.status != 200: return None
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                
                return {
                    "Categoria": "Medicamentos",
                    "Nome": achar_nome(soup),
                    "Marca": achar_marca(soup),
                    "EAN": achar_EAN_gtin(soup),
                    "Preço sem desconto": achar_preco_antes_desconto(soup),
                    "Desconto (%)": achar_desconto(soup),
                    "Preço com Desconto": achar_preco(soup),
                    "Preço Pix": achar_pix(soup),
                    "Cashback": achar_cashback(soup),
                    "Apenas PIX": achar_apenas_pix(soup),
                    "Promoção por Volume": achar_promo_volume(soup),
                    "Link": url
                }
        except:
            return None

async def main():
    links_produtos = []
    pagina = 1

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # 1. Coleta de Links
        print("=== MAPEANDO PÁGINAS ===")
        while True:
            try:
                async with session.get(BASE_URL.format(pagina)) as response:
                    if response.status != 200: break
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    produtos = soup.select(".item-product")
                    
                    if not produtos: break
                    
                    for p in produtos:
                        link_tag = p.select_one(".title a")
                        if link_tag:
                            href = link_tag['href']
                            links_produtos.append(f"https://www.farmaponte.com.br{href}" if href.startswith("/") else href)
                    
                    print(f"Lendo página {pagina}... Total acumulado: {len(links_produtos)} links", end="\r")
                    pagina += 1
            except: break

        print(f"\n\nTotal para processar: {len(links_produtos)}")
        print("-" * 50)

        # 2. Extração com TQDM
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tarefas = [processar_produto(session, link, semaphore) for link in links_produtos]
        
        dados_finais = []
        # tqdm.as_completed permite ver a barra de progresso conforme as tasks terminam
        for f in tqdm.as_completed(tarefas, total=len(tarefas), desc="Extraindo Produtos"):
            item = await f
            if item:
                dados_finais.append(item)

    # 3. Exportação
    if dados_finais:
        df = pd.DataFrame(dados_finais)
        df.drop_duplicates(subset=["Link"], inplace=True)
        df.to_excel("medicamentos_farmaponte.xlsx", index=False)
        print(f"\nConcluído! {len(df)} produtos salvos.")
    else:
        print("\nNenhum dado coletado.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    inicio = time.time()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    print(f"\nTempo total: {time.time() - inicio:.2f}s")