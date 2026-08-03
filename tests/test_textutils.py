from podcast.textutils import (
    canonical_url,
    clean_summary,
    normalize_title,
    stable_id,
    strip_html,
    title_similarity,
    truncate,
    unwrap_redirect,
)


class TestStripHtml:
    def test_remove_tags_e_normaliza_espacos(self):
        html = "<p>O Copom  manteve\n a <strong>Selic</strong>.</p>"
        assert strip_html(html) == "O Copom manteve a Selic."

    def test_resolve_entidades(self):
        assert strip_html("Juros &amp; c&acirc;mbio") == "Juros & câmbio"

    def test_descarta_conteudo_de_script(self):
        assert "alert" not in strip_html("<p>Texto</p><script>alert(1)</script>")

    def test_nbsp_vira_espaco_comum(self):
        assert strip_html("a\xa0b") == "a b"

    def test_entrada_vazia_ou_none(self):
        assert strip_html("") == ""
        assert strip_html(None) == ""

    def test_html_quebrado_nao_levanta(self):
        assert "texto" in strip_html("<p><b>texto</p</b")


class TestCleanSummary:
    """Casos observados na coleta real de 2026-08-03."""

    def test_remove_boilerplate_do_wordpress(self):
        texto = ("Minério recua 2,33%, para US$93,65 por tonelada "
                 "The post Minério amplia queda appeared first on InfoMoney.")
        assert clean_summary(texto) == "Minério recua 2,33%, para US$93,65 por tonelada"

    def test_remove_rodape_leia_mais_da_folha(self):
        texto = "A Shell acertou a venda da unidade. Leia mais (08/03/2026 - 09h53)"
        assert clean_summary(texto) == "A Shell acertou a venda da unidade."

    def test_remove_caracteres_invisiveis(self):
        # U+2060 (word joiner) e U+200B (zero-width space), vistos no InfoMoney
        assert clean_summary("ferro para⁠ setembro​ na Bolsa") == (
            "ferro para setembro na Bolsa"
        )

    def test_remove_continue_lendo(self):
        assert clean_summary("Texto do resumo. Continue lendo...") == "Texto do resumo."

    def test_texto_limpo_passa_intacto(self):
        texto = "O Copom manteve a Selic em 15% ao ano."
        assert clean_summary(texto) == texto

    def test_nao_remove_a_palavra_post_no_meio(self):
        texto = "The post office anunciou mudanças nas tarifas."
        assert clean_summary(texto) == texto

    def test_entrada_vazia(self):
        assert clean_summary("") == ""


class TestTruncate:
    def test_texto_curto_passa_intacto(self):
        assert truncate("curto", 100) == "curto"

    def test_corta_em_limite_de_palavra(self):
        resultado = truncate("um dois tres quatro cinco", 12)
        assert resultado.endswith("…")
        assert "quatr" not in resultado  # nao cortou no meio da palavra


class TestCanonicalUrl:
    def test_remove_parametros_de_rastreamento(self):
        assert canonical_url(
            "https://ex.com/noticia?utm_source=rss&utm_medium=feed&id=42"
        ) == "https://ex.com/noticia?id=42"

    def test_remove_fragmento_e_barra_final(self):
        assert canonical_url("https://ex.com/noticia/#comentarios") == "https://ex.com/noticia"

    def test_normaliza_caixa_do_dominio(self):
        assert canonical_url("HTTPS://Ex.COM/Noticia") == "https://ex.com/Noticia"

    def test_mesma_noticia_com_rastreios_diferentes_colapsa(self):
        a = canonical_url("https://ex.com/x?utm_source=twitter")
        b = canonical_url("https://ex.com/x?fbclid=abc")
        assert a == b

    def test_url_vazia(self):
        assert canonical_url("") == ""

    def test_desembrulha_redirect_da_folha(self):
        # Caso real observado na coleta de 2026-08-03.
        wrapper = (
            "https://redir.folha.com.br/redir/online/mercado/rss091/"
            "*https://www1.folha.uol.com.br/mercado/2026/08/shell-vende.shtml"
        )
        assert canonical_url(wrapper) == (
            "https://www1.folha.uol.com.br/mercado/2026/08/shell-vende.shtml"
        )

    def test_mesmo_artigo_por_redirects_diferentes_colapsa(self):
        a = "https://redir.folha.com.br/redir/online/mercado/rss091/*https://f.com/x"
        b = "https://redir.folha.com.br/redir/online/mundo/rss091/*https://f.com/x"
        assert canonical_url(a) == canonical_url(b)


class TestUnwrapRedirect:
    def test_url_normal_passa_intacta(self):
        assert unwrap_redirect("https://ex.com/a") == "https://ex.com/a"

    def test_usa_o_ultimo_marcador(self):
        url = "https://r.com/a/*https://s.com/b/*https://final.com/c"
        assert unwrap_redirect(url) == "https://final.com/c"

    def test_asterisco_sem_url_nao_e_desembrulhado(self):
        assert unwrap_redirect("https://ex.com/a*b") == "https://ex.com/a*b"


class TestTitleSimilarity:
    def test_titulos_identicos(self):
        assert title_similarity("Copom mantém Selic", "Copom mantém Selic") == 1.0

    def test_mesma_noticia_manchetes_diferentes(self):
        a = "Copom mantém Selic em 15% ao ano pela terceira reunião seguida"
        b = "Copom mantém a Selic em 15% ao ano na terceira reunião consecutiva"
        assert title_similarity(a, b) >= 0.75

    def test_noticias_distintas(self):
        a = "Copom mantém Selic em 15%"
        b = "Ibovespa fecha em alta puxado por bancos"
        assert title_similarity(a, b) < 0.3

    def test_ignora_acento_e_pontuacao(self):
        assert title_similarity("Inflação cai", "inflacao cai") == 1.0

    def test_titulo_vazio(self):
        assert title_similarity("", "qualquer coisa") == 0.0


class TestNormalizeTitle:
    def test_remove_acento_e_pontuacao(self):
        assert normalize_title("Inflação: 4,5%!") == "inflacao 4 5"


class TestStableId:
    def test_determinista(self):
        assert stable_id("https://ex.com/x") == stable_id("https://ex.com/x")

    def test_entradas_distintas_geram_ids_distintos(self):
        assert stable_id("https://ex.com/a") != stable_id("https://ex.com/b")

    def test_tamanho_fixo(self):
        assert len(stable_id("qualquer")) == 16
