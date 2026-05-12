import asyncio
import aiohttp
import json
import time
import sys
import pandas as pd
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

BASE_URL = "https://www.farmaponte.com.br/saude/medicamentos/?p={}"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive'
}
CONCURRENCY_LIMIT = 15

#funções
def limpar_preco(texto):
    if not texto: return "0"
    return texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()

def limpar_porcentagem(texto):
    if not texto: return "0"
    return texto.replace("%", "").replace("-", "").replace(" ", "").strip()

#Extração
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

#ASync
async def processar_produto(session, url, semaphore):
    async with semaphore:
        try:
            # Timeout mais curto para não travar o script em páginas lentas
            async with session.get(url, timeout=15) as response:
                if response.status != 200: return None
                
                #lxml
                content = await response.read()
                soup = BeautifulSoup(content, "lxml")
                
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
    
    connector = aiohttp.TCPConnector(limit_per_host=CONCURRENCY_LIMIT, ssl=False)
    
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        #Link
        pagina = 1
        print("=== MAPEANDO PÁGINAS (FASE 1) ===")
        while True:
            try:
                async with session.get(BASE_URL.format(pagina), timeout=15) as response:
                    if response.status != 200: break
                    
                    soup = BeautifulSoup(await response.read(), "lxml")
                    produtos = soup.select(".item-product")
                    
                    if not produtos:
                        print(f"\nFim da paginação na página {pagina-1}.")
                        break
                    
                    for p in produtos:
                        link_tag = p.select_one(".title a")
                        if link_tag:
                            href = link_tag['href']
                            links_produtos.append(f"https://www.farmaponte.com.br{href}" if href.startswith("/") else href)
                    
                    print(f"Lendo página {pagina}... Total acumulado: {len(links_produtos)} links", end="\r")
                    pagina += 1
            except:
                break

        print(f"\n\nTotal de produtos únicos encontrados: {len(set(links_produtos))}")
        print("=== EXTRAINDO DETALHES (FASE 2) ===")
        print("-" * 50)

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        links_unicos = list(set(links_produtos))
        tarefas = [processar_produto(session, link, semaphore) for link in links_unicos]
        
        dados_finais = []
        for f in tqdm.as_completed(tarefas, total=len(tarefas), desc="Progresso"):
            item = await f
            if item:
                dados_finais.append(item)

    # 3. Exportação
    if dados_finais:
        df = pd.DataFrame(dados_finais)
        colunas = ["Categoria", "Nome", "Marca", "EAN", "Preço sem desconto", 
                   "Desconto (%)", "Preço com Desconto", "Preço Pix", 
                   "Cashback", "Apenas PIX", "Promoção por Volume", "Link"]
        df = df[colunas]
        
        arquivo = "medicamentos_farmaponte_final.xlsx"
        df.to_excel(arquivo, index=False)
        print("\n" + "="*50)
        print(f"SUCESSO! {len(df)} produtos extraídos.")
        print(f"Arquivo salvo como: {arquivo}")
        print("="*50)
    else:
        print("\nNenhum dado foi extraído com sucesso.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    inicio = time.time()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProcesso interrompido manualmente.")
    
    print(f"\nTempo total de execução: {time.time() - inicio:.2f}s")