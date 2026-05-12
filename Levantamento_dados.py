import asyncio
import aiohttp
import json
import time
import sys
import pandas as pd
from bs4 import BeautifulSoup

# --- Configurações ---
BASE_URL = "https://www.farmaponte.com.br/saude/medicamentos/?p={}"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
}
CONCURRENCY_LIMIT = 15 # Quantidade de requisições simultâneas

# --- Funções Auxiliares de Limpeza ---

def limpar_preco(texto):
    if not texto: return "0"
    # Remove R$, espaços e converte formato decimal se necessário
    return texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()

def limpar_porcentagem(texto):
    if not texto: return "0"
    return texto.replace("%", "").replace("-", "").replace(" ", "").strip()

# --- Funções de Extração ---

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
        if selo_pix and "PIX" in selo_pix.get_text(strip=True).upper():
            return "Sim"
        return "Não"
    except: return "Não"

def achar_promo_volume(soup):
    try:
        for b in soup.find_all('b'):
            texto = b.get_text(strip=True)
            if "A PARTIR DE" in texto.upper():
                return " ".join(texto.split()) 
        return "NA"
    except: return "NA"

# --- Processamento Assíncrono ---

async def processar_produto(session, url, semaphore):
    async with semaphore:
        try:
            async with session.get(url, timeout=20) as response:
                if response.status != 200: return None
                
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                nome = achar_nome(soup)
                ean = achar_EAN_gtin(soup)
                
                item = {
                    "Categoria": "Medicamentos",
                    "Nome": nome,
                    "Marca": achar_marca(soup),
                    "EAN": ean,
                    "Preço sem desconto": achar_preco_antes_desconto(soup),
                    "Desconto (%)": achar_desconto(soup),
                    "Preço com Desconto": achar_preco(soup),
                    "Preço Pix": achar_pix(soup),
                    "Cashback": achar_cashback(soup),
                    "Apenas PIX": achar_apenas_pix(soup),
                    "Promoção por Volume": achar_promo_volume(soup),
                    "Link": url
                }

                print(f" [OK] {nome[:30]:<30} | EAN: {ean}")
                return item
        except Exception as e:
            print(f" [ERRO] {url}: {e}")
            return None

async def main():
    links_produtos = []
    pagina = 1

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # 1. Coleta de Links (Paginação)
        print("=== INICIANDO MAPEAMENTO DE PRODUTOS ===")
        while True:
            try:
                async with session.get(BASE_URL.format(pagina)) as response:
                    if response.status != 200: break
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    produtos = soup.select(".item-product")
                    
                    if not produtos:
                        print(f"\nFim da paginação na página {pagina-1}.")
                        break
                    
                    for p in produtos:
                        try: 
                            link_tag = p.select_one(".title a")
                            if link_tag:
                                href = link_tag['href']
                                link_completo = f"https://www.farmaponte.com.br{href}" if href.startswith("/") else href
                                links_produtos.append(link_completo)
                        except: continue
                    
                    print(f"Lendo página {pagina}... Total: {len(links_produtos)} links", end="\r")
                    pagina += 1
            except Exception as e:
                print(f"\nErro na paginação: {e}")
                break

        print(f"\n\nTotal de produtos encontrados: {len(links_produtos)}")
        print("-" * 50)

        # 2. Extração de Detalhes
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        tarefas = [processar_produto(session, link, semaphore) for link in links_produtos]
        
        resultados = await asyncio.gather(*tarefas)
        dados_finais = [r for r in resultados if r is not None]

    # 3. Exportação de Dados
    if dados_finais:
        df = pd.DataFrame(dados_finais)
        df.drop_duplicates(subset=["Link"], inplace=True)
        
        nome_arquivo = "medicamentos_farmaponte.xlsx"
        df.to_excel(nome_arquivo, index=False)
        
        print("\n" + "="*50)
        print(f"Sucesso! {len(df)} produtos salvos em '{nome_arquivo}'.")
        print("="*50)
    else:
        print("\nNenhum dado foi extraído.")

if __name__ == "__main__":
    inicio = time.time()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    
    
    
    print(f"\nTempo total de execução: {time.time() - inicio:.2f}s")