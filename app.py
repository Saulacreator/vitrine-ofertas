import json
import os
import re
import textwrap
from datetime import datetime
from io import BytesIO
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

st.set_page_config(page_title="Central de Ofertas & Vitrine", page_icon="🏷️", layout="wide")

DB_FILE = "vitrine_ofertas.json"
CONFIG_FILE = "config_afiliado.json"


# --- PERSISTÊNCIA LOCAL (JSON) ---
def carregar_json(caminho: str, padrao):
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return padrao
    return padrao


def salvar_json(caminho: str, dados):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# --- CONVERSOR DE LINKS DE AFILIADO ---
def converter_link_afiliado(url: str, config: dict) -> str:
    url = url.strip()
    if not url:
        return url

    parsed = urlparse(url)
    dominio = parsed.netloc.lower()

    # 1. Amazon
    amazon_tag = config.get("amazon_tag", "").strip()
    if "amazon.com.br" in dominio and amazon_tag:
        query = parse_qs(parsed.query)
        query["tag"] = [amazon_tag]
        nova_query = urlencode(query, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", nova_query, ""))

    # 2. Awin
    awin_id = config.get("awin_id", "").strip()
    lojas_awin = config.get("awin_lojas", {})
    if awin_id and lojas_awin:
        for dom_loja, mid in lojas_awin.items():
            dom_limpo = dom_loja.strip().lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
            if dom_limpo and dom_limpo in dominio and str(mid).strip():
                return f"https://www.awin1.com/cread.php?awinmid={str(mid).strip()}&awinaffid={awin_id}&ued={quote(url, safe='')}"

    # 3. Mercado Livre
    ml_tool = config.get("ml_matt_tool", "").strip()
    ml_word = config.get("ml_matt_word", "").strip()
    if ("mercadolivre.com.br" in dominio or "produto.mercadolivre.com.br" in dominio) and ml_tool:
        query = parse_qs(parsed.query)
        query["matt_tool"] = [ml_tool]
        if ml_word:
            query["matt_word"] = [ml_word]
        nova_query = urlencode(query, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", nova_query, ""))

    return url


# --- EXTRATOR DE DADOS (API ML + OPENGRAPH) ---
def formatar_brl(valor) -> str:
    try:
        v = float(str(valor).replace(",", "."))
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return ""


def extrair_dados_produto(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    match_mlb = re.search(r"(MLB-?\d+)", url, flags=re.I)
    if "mercadolivre" in url.lower() and match_mlb:
        mlb_id = match_mlb.group(1).replace("-", "").upper()
        try:
            api_resp = requests.get(f"https://api.mercadolibre.com/items/{mlb_id}", timeout=8)
            if api_resp.status_code == 200:
                item = api_resp.json()
                fotos = item.get("pictures", [])
                img_url = fotos[0].get("secure_url") if fotos else item.get("thumbnail", "")
                preco_atual = formatar_brl(item.get("price"))
                preco_orig = formatar_brl(item.get("original_price")) if item.get("original_price") else ""
                return {
                    "titulo": item.get("title", ""),
                    "imagem": img_url,
                    "preco": preco_atual,
                    "preco_antigo": preco_orig,
                    "loja": "Mercado Livre",
                }
        except Exception:
            pass

    resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def meta_content(prop=None, name=None):
        tag = soup.find("meta", property=prop) if prop else soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else ""

    titulo = meta_content(prop="og:title") or meta_content(name="twitter:title")
    if not titulo and soup.title:
        titulo = soup.title.get_text(strip=True)
    titulo = re.sub(r"\s*[\|\-:]\s*(Amazon\.com\.br|Mercado Livre|Netshoes|Centauro|Nike|Magalu).*$", "", titulo, flags=re.I)

    imagem = meta_content(prop="og:image") or meta_content(name="twitter:image")
    if not imagem:
        img_tag = soup.find("img", id="landingImage") or soup.find("img", id="imgBlkFront")
        if img_tag:
            imagem = img_tag.get("data-old-hires") or img_tag.get("src") or ""
    if not imagem:
        ml_img = soup.find("img", class_=re.compile("ui-pdp-image"))
        if ml_img:
            imagem = ml_img.get("data-zoom") or ml_img.get("src") or ""

    preco = formatar_brl(meta_content(prop="product:price:amount") or meta_content(prop="og:price:amount"))
    dominio = urlparse(resp.url).netloc.replace("www.", "").split(".")[0].capitalize()

    return {
        "titulo": titulo,
        "imagem": imagem,
        "preco": preco,
        "preco_antigo": "",
        "loja": dominio,
    }


# --- GERADOR DE ARTE PARA STORIES (1080x1920) ---
def carregar_fonte(tamanho: int, negrito: bool = False):
    caminhos_possiveis = [
        "C:/Windows/Fonts/arialbd.ttf" if negrito else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if negrito else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for caminho in caminhos_possiveis:
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()


def gerar_arte_story(
    titulo: str,
    preco: str,
    preco_antigo: str,
    cupom: str,
    chamada: str,
    img_bytes: bytes | None,
    cor_fundo: str,
    cor_destaque: str,
    arroba: str,
) -> bytes:
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), cor_fundo)
    draw = ImageDraw.Draw(canvas)

    f_arroba = carregar_fonte(36, negrito=True)
    f_badge = carregar_fonte(40, negrito=True)
    f_titulo = carregar_fonte(46, negrito=True)
    f_de = carregar_fonte(38, negrito=False)
    f_por = carregar_fonte(76, negrito=True)
    f_cupom = carregar_fonte(44, negrito=True)
    f_cta = carregar_fonte(38, negrito=True)

    if arroba:
        draw.text((W // 2, 95), arroba, fill="#FFFFFF", font=f_arroba, anchor="mm")

    if chamada:
        draw.rounded_rectangle([140, 155, W - 140, 235], radius=35, fill=cor_destaque)
        draw.text((W // 2, 195), chamada.upper()[:28], fill="#111111", font=f_badge, anchor="mm")

    card_top, card_bottom = 270, 1520
    draw.rounded_rectangle([70, card_top, W - 70, card_bottom], radius=50, fill="#FFFFFF")

    if img_bytes:
        try:
            prod_img = Image.open(BytesIO(img_bytes)).convert("RGBA")
            prod_img.thumbnail((760, 640), Image.Resampling.LANCZOS)
            fundo_branco = Image.new("RGB", prod_img.size, "#FFFFFF")
            fundo_branco.paste(prod_img, mask=prod_img.split()[3])
            px = (W - fundo_branco.width) // 2
            py = card_top + 60 + (640 - fundo_branco.height) // 2
            canvas.paste(fundo_branco, (px, py))
        except Exception:
            pass

    y_texto = 1010
    linhas_titulo = textwrap.wrap(titulo, width=32)[:3]
    for linha in linhas_titulo:
        draw.text((W // 2, y_texto), linha, fill="#1E1E1E", font=f_titulo, anchor="mm")
        y_texto += 58

    y_texto += 25
    if preco_antigo:
        txt_de = f"De: {preco_antigo}"
        draw.text((W // 2, y_texto), txt_de, fill="#888888", font=f_de, anchor="mm")
        bbox = draw.textbbox((W // 2, y_texto), txt_de, font=f_de, anchor="mm")
        draw.line([(bbox[0], y_texto), (bbox[2], y_texto)], fill="#888888", width=3)
        y_texto += 65

    if preco:
        draw.text((W // 2, y_texto + 15), preco, fill="#0D9488", font=f_por, anchor="mm")
        y_texto += 105

    if cupom:
        draw.rounded_rectangle([200, y_texto, W - 200, y_texto + 85], radius=24, fill="#FEF3C7", outline=cor_destaque, width=4)
        draw.text((W // 2, y_texto + 42), f"CUPOM: {cupom}", fill="#92400E", font=f_cupom, anchor="mm")

    draw.rounded_rectangle([180, 1585, W - 180, 1715], radius=30, outline="#FFFFFF", width=3)
    draw.text((W // 2, 1650), "👇 COLE A FIGURINHA DE LINK AQUI", fill="#FFFFFF", font=f_cta, anchor="mm")

    saida = BytesIO()
    canvas.save(saida, format="PNG")
    return saida.getvalue()


# --- TEXTOS DE DIVULGAÇÃO ---
def gerar_textos(titulo: str, preco: str, preco_antigo: str, cupom: str, chamada: str, link: str) -> tuple[str, str]:
    linhas_wpp = []
    if chamada:
        linhas_wpp.append(f"🔥 *{chamada.upper()}*")
    linhas_wpp.append(f"*{titulo}*")
    if preco_antigo:
        linhas_wpp.append(f"~De: {preco_antigo}~")
    if preco:
        linhas_wpp.append(f"✅ *Por: {preco}*")
    if cupom:
        linhas_wpp.append(f"🎟️ Cupom: *{cupom}*")
    linhas_wpp.append(f"🛒 Compre aqui: {link}")

    linhas_ig = [titulo]
    if preco:
        linhas_ig.append(f"Por {preco}")
    if cupom:
        linhas_ig.append(f"Use o cupom: {cupom}")
    linhas_ig.append("👇 Toque no link para aproveitar")

    return "\n".join(linhas_wpp), "\n".join(linhas_ig)


# --- INICIALIZAÇÃO DE ESTADO ---
config_padrao = {
    "arroba": "@corridametabolica",
    "cor_fundo": "#111827",
    "cor_destaque": "#FBBF24",
    "amazon_tag": "",
    "awin_id": "",
    "awin_lojas": {"nike.com.br": "", "centauro.com.br": "", "netshoes.com.br": ""},
    "ml_matt_tool": "",
    "ml_matt_word": "",
    "senha_admin": "SUA_SENHA_AQUI",
}
config = carregar_json(CONFIG_FILE, config_padrao)
vitrine = carregar_json(DB_FILE, [])

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

# --- BARRA LATERAL (LOGIN DO ADMINISTRADOR) ---
with st.sidebar:
    st.markdown("### 🔐 Acesso do Criador")
    if not st.session_state["autenticado"]:
        senha_digitada = st.text_input("Senha de administrador:", type="password", placeholder="Digite a senha...")
        if st.button("Entrar no Painel", use_container_width=True):
            try:
                senha_correta = st.secrets["ADMIN_PASSWORD"]
            except Exception:
                senha_correta = config.get("senha_admin", "SUA_SENHA_AQUI")

            if senha_digitada == senha_correta:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
        st.caption("Visitantes navegam livremente pela Vitrine ao lado.")
    else:
        st.success("Modo Administrador ativo")
        if st.button("Sair (Bloquear Painel)", use_container_width=True):
            st.session_state["autenticado"] = False
            st.rerun()


# --- RENDERIZAÇÃO DA VITRINE (FUNÇÃO REUTILIZÁVEL) ---
def renderizar_vitrine(modo_admin: bool):
    st.subheader(f"🛍️ Achadinhos & Cupons — {config['arroba']}")
    col_busca, col_cat = st.columns([2.2, 1])
    with col_busca:
        busca = st.text_input("🔍 Buscar produto ou loja...", placeholder="Ex: tênis, creatina, air fryer...")
    with col_cat:
        filtro_cat = st.selectbox("Filtrar categoria", ["Todas", "Corrida & Esporte", "Suplementos", "Casa & Cozinha", "Outros", "Geral"])

    itens_filtrados = [
        item for item in vitrine
        if (filtro_cat == "Todas" or item.get("categoria") == filtro_cat)
        and (not busca or busca.lower() in item.get("titulo", "").lower() or busca.lower() in item.get("loja", "").lower())
    ]

    if not itens_filtrados:
        st.info("Nenhuma oferta publicada no momento.")
    else:
        cols = st.columns(3)
        for i, item in enumerate(itens_filtrados):
            with cols[i % 3]:
                with st.container(border=True):
                    if item.get("imagem"):
                        st.image(item["imagem"], use_container_width=True)
                    st.caption(f"{item.get('loja', '')} • {item.get('categoria', '')}")
                    st.markdown(f"**{item.get('titulo', '')[:75]}**")
                    if item.get("preco_antigo"):
                        st.caption(f"~~{item['preco_antigo']}~~")
                    if item.get("preco"):
                        st.markdown(f"### {item['preco']}")
                    if item.get("cupom"):
                        st.code(f"Cupom: {item['cupom']}", language=None)
                    st.link_button("🛒 Pegar Oferta", item["link"], use_container_width=True)

                    if modo_admin:
                        if st.button("❌ Remover", key=f"del_{item.get('id', i)}", use_container_width=True):
                            vitrine.remove(item)
                            salvar_json(DB_FILE, vitrine)
                            st.rerun()


# ==============================================================================
# VISÃO PÚBLICA (VISITANTES SEM LOGIN) VS VISÃO ADMINISTRADOR (COM LOGIN)
# ==============================================================================
if not st.session_state["autenticado"]:
    renderizar_vitrine(modo_admin=False)
else:
    aba_vitrine, aba_gerador, aba_lote, aba_config = st.tabs(
        ["🛍️ Vitrine (Link na Bio)", "⚡ Gerador + Arte Story", "📦 Modo Lote", "⚙️ Minhas Tags & Senha"]
    )

    # ABA 1: VITRINE
    with aba_vitrine:
        renderizar_vitrine(modo_admin=True)

    # ABA 2: GERADOR INDIVIDUAL + ARTE STORY
    with aba_gerador:
        st.subheader("Gerar Oferta, Arte para Stories e Copy")
        with st.form("form_individual"):
            url_entrada = st.text_input("🔗 Cole o link do produto (link comum da loja ou link já afiliado) *")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                preco_in = st.text_input("💰 Preço atual", placeholder="R$ 199,90")
            with c2:
                preco_ant_in = st.text_input("📉 Preço anterior", placeholder="R$ 299,90")
            with c3:
                cupom_in = st.text_input("🎟️ Cupom", placeholder="CORRIDA15")
            with c4:
                categoria_in = st.selectbox("📂 Categoria", ["Corrida & Esporte", "Suplementos", "Casa & Cozinha", "Outros"])

            chamada_in = st.text_input("✨ Chamada de destaque", placeholder="Ex: Menor preço do mês! / Frete Grátis")
            btn_gerar = st.form_submit_button("Gerar Oferta Completa", use_container_width=True)

        if btn_gerar and url_entrada.strip():
            with st.spinner("Convertendo link e extraindo dados do produto..."):
                link_convertido = converter_link_afiliado(url_entrada.strip(), config)
                try:
                    dados = extrair_dados_produto(url_entrada.strip())
                except Exception as e:
                    st.warning(f"Leitura parcial da página ({e}). Você pode ajustar os campos abaixo.")
                    dados = {"titulo": "", "imagem": "", "preco": "", "preco_antigo": "", "loja": "Loja"}

                st.session_state["oferta_atual"] = {
                    "titulo": dados["titulo"],
                    "imagem": dados["imagem"],
                    "preco": preco_in.strip() or dados["preco"],
                    "preco_antigo": preco_ant_in.strip() or dados["preco_antigo"],
                    "cupom": cupom_in.strip().upper(),
                    "chamada": chamada_in.strip(),
                    "categoria": categoria_in,
                    "loja": dados["loja"],
                    "link": link_convertido,
                }

        if "oferta_atual" in st.session_state:
            of = st.session_state["oferta_atual"]
            st.divider()
            col_edit, col_preview = st.columns([1.2, 1])

            with col_edit:
                titulo_ed = st.text_input("📝 Título", value=of["titulo"])
                link_ed = st.text_input("🔗 Link de Afiliado Final", value=of["link"])
                img_ed = st.text_input("🖼️ URL da Imagem", value=of["imagem"])
                c_p1, c_p2, c_p3 = st.columns(3)
                with c_p1:
                    preco_ed = st.text_input("Por:", value=of["preco"])
                with c_p2:
                    antigo_ed = st.text_input("De:", value=of["preco_antigo"])
                with c_p3:
                    cupom_ed = st.text_input("Cupom:", value=of["cupom"])

                img_bytes = None
                if img_ed:
                    try:
                        r_img = requests.get(img_ed, timeout=10)
                        if r_img.status_code == 200:
                            img_bytes = r_img.content
                    except Exception:
                        pass

                txt_wpp, txt_ig = gerar_textos(titulo_ed, preco_ed, antigo_ed, cupom_ed, of["chamada"], link_ed)

                st.markdown("**📲 Copiar para WhatsApp / Telegram:**")
                st.code(txt_wpp, language=None)

                st.markdown("**📸 Copiar para Stories / Link:**")
                st.code(txt_ig, language=None)
                st.code(link_ed, language=None)

                if st.button("💾 Publicar na Minha Vitrine (Link na Bio)", use_container_width=True):
                    novo_item = {
                        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "titulo": titulo_ed,
                        "preco": preco_ed,
                        "preco_antigo": antigo_ed,
                        "cupom": cupom_ed,
                        "categoria": of["categoria"],
                        "loja": of["loja"],
                        "imagem": img_ed,
                        "link": link_ed,
                    }
                    vitrine.insert(0, novo_item)
                    salvar_json(DB_FILE, vitrine)
                    st.success("Oferta publicada na Vitrine!")

            with col_preview:
                st.markdown("**🎨 Arte Pronta para Stories (1080×1920)**")
                arte_png = gerar_arte_story(
                    titulo=titulo_ed or "Oferta Imperdível",
                    preco=preco_ed,
                    preco_antigo=antigo_ed,
                    cupom=cupom_ed,
                    chamada=of["chamada"],
                    img_bytes=img_bytes,
                    cor_fundo=config["cor_fundo"],
                    cor_destaque=config["cor_destaque"],
                    arroba=config["arroba"],
                )
                st.image(arte_png, width=320)
                st.download_button(
                    label="⬇️ Baixar Arte Story (PNG)",
                    data=arte_png,
                    file_name=f"story_{datetime.now().strftime('%H%M%S')}.png",
                    mime="image/png",
                    use_container_width=True,
                )

    # ABA 3: PROCESSAMENTO EM LOTE
    with aba_lote:
        st.subheader("Gerador em Lote (Vários Links de Uma Vez)")
        st.caption("Cole um link por linha. O sistema converte todos para seus links de afiliado, extrai os títulos/preços e gera a lista completa para disparar nos grupos.")

        links_lote = st.text_area("Cole os links aqui (1 por linha):", height=160, placeholder="https://www.amazon.com.br/dp/...\nhttps://www.mercadolivre.com.br/...")
        cupom_lote = st.text_input("🎟️ Cupom geral para o lote (opcional):")
        salvar_lote_vitrine = st.checkbox("Salvar todos automaticamente na Vitrine", value=True)

        if st.button("Processar Todos os Links", use_container_width=True):
            lista_urls = [u.strip() for u in links_lote.splitlines() if u.strip()]
            if not lista_urls:
                st.warning("Insira pelo menos um link.")
            else:
                blocos_wpp = []
                progresso = st.progress(0)
                for idx, url in enumerate(lista_urls):
                    link_conv = converter_link_afiliado(url, config)
                    try:
                        d = extrair_dados_produto(url)
                    except Exception:
                        d = {"titulo": f"Oferta {idx+1}", "imagem": "", "preco": "", "preco_antigo": "", "loja": "Loja"}

                    wpp_item, _ = gerar_textos(d["titulo"], d["preco"], d["preco_antigo"], cupom_lote.strip().upper(), "", link_conv)
                    blocos_wpp.append(wpp_item)

                    if salvar_lote_vitrine:
                        vitrine.insert(0, {
                            "id": f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{idx}",
                            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "titulo": d["titulo"],
                            "preco": d["preco"],
                            "preco_antigo": d["preco_antigo"],
                            "cupom": cupom_lote.strip().upper(),
                            "categoria": "Geral",
                            "loja": d["loja"],
                            "imagem": d["imagem"],
                            "link": link_conv,
                        })
                    progresso.progress((idx + 1) / len(lista_urls))

                if salvar_lote_vitrine:
                    salvar_json(DB_FILE, vitrine)

                st.success(f"{len(lista_urls)} ofertas processadas!")
                st.code("\n\n━━━━━━━━━━━━━━━\n\n".join(blocos_wpp), language=None)

    # ABA 4: CONFIGURAÇÕES DE AFILIADO, VISUAL E SENHA
    with aba_config:
        st.subheader("Configurar Conversão Automática, Visual e Senha")
        with st.form("form_config"):
            st.markdown("#### 🔐 Segurança do Painel")
            senha_cfg = st.text_input("Senha de administrador", value=config.get("senha_admin", "SUA_SENHA_AQUI"), type="password")

            st.divider()
            st.markdown("#### 🎨 Identidade Visual dos Stories")
            c_v1, c_v2, c_v3 = st.columns(3)
            with c_v1:
                arroba_cfg = st.text_input("Seu @ do Instagram", value=config["arroba"])
            with c_v2:
                cor_fundo_cfg = st.color_picker("Cor de fundo do Story", value=config["cor_fundo"])
            with c_v3:
                cor_dest_cfg = st.color_picker("Cor de destaque / cupom", value=config["cor_destaque"])

            st.divider()
            st.markdown("#### 🔗 Credenciais para Conversão Automática de Links")
            amazon_cfg = st.text_input("Amazon Associados — Sua Tag (ex: seunome-20)", value=config["amazon_tag"])

            c_ml1, c_ml2 = st.columns(2)
            with c_ml1:
                ml_tool_cfg = st.text_input("Mercado Livre — matt_tool (opcional)", value=config["ml_matt_tool"])
            with c_ml2:
                ml_word_cfg = st.text_input("Mercado Livre — matt_word (opcional)", value=config["ml_matt_word"])

            awin_id_cfg = st.text_input("Awin — Seu ID de Afiliado (awinaffid)", value=config["awin_id"])
            st.caption("IDs dos anunciantes Awin (awinmid) por domínio — adicione ou edite conforme suas lojas aprovadas:")
            awin_texto_atual = "\n".join([f"{k}={v}" for k, v in config.get("awin_lojas", {}).items()])
            awin_lojas_txt = st.text_area("Formato: dominio.com.br=ID_DO_ANUNCIANTE (1 por linha)", value=awin_texto_atual, height=110)

            if st.form_submit_button("Salvar Configurações", use_container_width=True):
                novo_mapa_awin = {}
                for linha in awin_lojas_txt.splitlines():
                    if "=" in linha:
                        k, v = linha.split("=", 1)
                        novo_mapa_awin[k.strip()] = v.strip()

                config = {
                    "senha_admin": senha_cfg.strip() or "SUA_SENHA_AQUI",
                    "arroba": arroba_cfg.strip(),
                    "cor_fundo": cor_fundo_cfg,
                    "cor_destaque": cor_dest_cfg,
                    "amazon_tag": amazon_cfg.strip(),
                    "awin_id": awin_id_cfg.strip(),
                    "awin_lojas": novo_mapa_awin,
                    "ml_matt_tool": ml_tool_cfg.strip(),
                    "ml_matt_word": ml_word_cfg.strip(),
                }
                salvar_json(CONFIG_FILE, config)
                st.success("Configurações salvas!")
