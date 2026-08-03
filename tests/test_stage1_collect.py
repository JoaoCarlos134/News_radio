"""Testes da etapa 1. Nenhum toca a rede: o fetcher e injetado."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from podcast.models import NewsItem
from podcast.stage1_collect import (
    SeenStore,
    collect,
    deduplicate,
    filter_by_window,
    load_collection,
    parse_feed,
    save_collection,
    sort_items,
)

from .conftest import FIXTURES, NOW


def _item(id_: str, title: str, link: str, minutos_atras: int = 0) -> NewsItem:
    return NewsItem(
        id=id_,
        source_key="teste",
        source_name="Teste",
        title=title,
        summary="",
        link=link,
        published_at=NOW - timedelta(minutes=minutos_atras),
    )


class TestParseFeed:
    def test_extrai_itens_validos(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        # 4 itens no XML, mas um nao tem link
        assert len(itens) == 3
        assert all(i.link for i in itens)

    def test_descarta_item_sem_link(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        assert not any("descartado" in i.title for i in itens)

    def test_resumo_vem_sem_html(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        copom = next(i for i in itens if "Copom" in i.title)
        assert "<" not in copom.summary
        assert "unanimidade" in copom.summary

    def test_link_e_canonicalizado(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        copom = next(i for i in itens if "Copom" in i.title)
        assert "utm_source" not in copom.link
        assert "id=42" in copom.link

    def test_data_em_utc(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        copom = next(i for i in itens if "Copom" in i.title)
        assert copom.published_at == datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)

    def test_item_sem_data_fica_com_none(self, agencia_source):
        itens = parse_feed(agencia_source, (FIXTURES / "agencia_brasil.xml").read_bytes())
        ipca = next(i for i in itens if "IPCA" in i.title)
        assert ipca.published_at is None

    def test_categorias_da_fonte_e_do_feed(self, infomoney_source):
        itens = parse_feed(infomoney_source, (FIXTURES / "infomoney.xml").read_bytes())
        copom = next(i for i in itens if "Copom" in i.title)
        assert "economia" in copom.categories   # da fonte
        assert "juros" in copom.categories      # do <category> do feed

    def test_id_estavel_entre_chamadas(self, infomoney_source):
        raw = (FIXTURES / "infomoney.xml").read_bytes()
        a = parse_feed(infomoney_source, raw)
        b = parse_feed(infomoney_source, raw)
        assert [i.id for i in a] == [i.id for i in b]

    def test_xml_invalido_nao_levanta(self, infomoney_source):
        # feedparser e tolerante; queremos apenas garantir que nao explode
        assert parse_feed(infomoney_source, b"nao sou xml") == []


class TestFilterByWindow:
    def test_mantem_dentro_da_janela(self):
        itens = [_item("a", "Recente", "https://ex.com/a", minutos_atras=60)]
        assert len(filter_by_window(itens, 24, now=NOW)) == 1

    def test_descarta_fora_da_janela(self):
        itens = [_item("a", "Antigo", "https://ex.com/a", minutos_atras=60 * 30)]
        assert filter_by_window(itens, 24, now=NOW) == []

    def test_mantem_item_sem_data(self):
        # Fontes oficiais (BCB, IBGE) frequentemente omitem pubDate.
        sem_data = NewsItem("a", "k", "Fonte", "T", "", "https://ex.com/a", None)
        assert len(filter_by_window([sem_data], 24, now=NOW)) == 1

    def test_descarta_data_absurda_no_futuro(self):
        futuro = NewsItem(
            "a", "k", "Fonte", "T", "", "https://ex.com/a",
            NOW + timedelta(days=3),
        )
        assert filter_by_window([futuro], 24, now=NOW) == []

    def test_tolera_relogio_levemente_adiantado(self):
        quase = NewsItem(
            "a", "k", "Fonte", "T", "", "https://ex.com/a",
            NOW + timedelta(hours=2),
        )
        assert len(filter_by_window([quase], 24, now=NOW)) == 1


class TestDeduplicate:
    def test_remove_id_repetido(self):
        itens = [
            _item("mesmo", "Titulo A", "https://ex.com/a"),
            _item("mesmo", "Titulo B totalmente diferente", "https://ex.com/b"),
        ]
        assert len(deduplicate(itens)) == 1

    def test_remove_mesma_url_com_rastreios_diferentes(self):
        itens = [
            _item("id1", "Titulo A", "https://ex.com/x?utm_source=rss"),
            _item("id2", "Outra manchete sem relacao alguma", "https://ex.com/x?fbclid=1"),
        ]
        assert len(deduplicate(itens)) == 1

    def test_remove_titulo_semelhante_de_veiculos_diferentes(self):
        itens = [
            _item("id1", "Copom mantém Selic em 15% ao ano pela terceira reunião seguida",
                  "https://a.com/1"),
            _item("id2", "Copom mantém a Selic em 15% ao ano na terceira reunião consecutiva",
                  "https://b.com/2"),
        ]
        assert len(deduplicate(itens)) == 1

    def test_preserva_noticias_distintas(self):
        itens = [
            _item("id1", "Copom mantém Selic em 15%", "https://a.com/1"),
            _item("id2", "Ibovespa fecha em alta puxado por bancos", "https://b.com/2"),
        ]
        assert len(deduplicate(itens)) == 2

    def test_mantem_a_primeira_ocorrencia(self):
        itens = [
            _item("id1", "Copom mantém Selic em 15% ao ano na terceira reunião", "https://a.com/1"),
            _item("id2", "Copom mantém a Selic em 15% ao ano na terceira reunião", "https://b.com/2"),
        ]
        assert deduplicate(itens)[0].id == "id1"

    def test_lista_vazia(self):
        assert deduplicate([]) == []


class TestSortItems:
    def test_mais_recentes_primeiro(self):
        itens = [
            _item("velho", "A", "https://ex.com/a", minutos_atras=600),
            _item("novo", "B", "https://ex.com/b", minutos_atras=10),
        ]
        assert [i.id for i in sort_items(itens)] == ["novo", "velho"]

    def test_sem_data_vai_para_o_fim(self):
        sem_data = NewsItem("x", "k", "F", "T", "", "https://ex.com/x", None)
        com_data = _item("y", "B", "https://ex.com/y", minutos_atras=1000)
        assert [i.id for i in sort_items([sem_data, com_data])] == ["y", "x"]


class TestSeenStore:
    def test_ida_e_volta_no_disco(self, tmp_path):
        caminho = tmp_path / "seen.json"
        store = SeenStore(caminho).load()
        store.mark([_item("abc", "T", "https://ex.com/a")])
        store.save()

        recarregado = SeenStore(caminho).load()
        assert "abc" in recarregado

    def test_filtra_itens_ja_vistos(self, tmp_path):
        store = SeenStore(tmp_path / "seen.json").load()
        store.mark([_item("visto", "T", "https://ex.com/a")])

        itens = [
            _item("visto", "T", "https://ex.com/a"),
            _item("novo", "U", "https://ex.com/b"),
        ]
        assert [i.id for i in store.filter_new(itens)] == ["novo"]

    def test_expurga_entradas_antigas(self, tmp_path):
        caminho = tmp_path / "seen.json"
        antigo = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        recente = datetime.now(timezone.utc).isoformat()
        caminho.write_text(json.dumps({"antigo": antigo, "recente": recente}))

        store = SeenStore(caminho, retention_days=7).load()
        assert "antigo" not in store
        assert "recente" in store

    def test_cache_corrompido_nao_derruba_a_coleta(self, tmp_path):
        caminho = tmp_path / "seen.json"
        caminho.write_text("{isso nao e json")
        store = SeenStore(caminho).load()  # nao deve levantar
        assert "qualquer" not in store

    def test_arquivo_inexistente(self, tmp_path):
        store = SeenStore(tmp_path / "nao-existe.json").load()
        assert "qualquer" not in store


class TestCollect:
    def test_agrega_multiplas_fontes(
        self, collect_config, infomoney_source, agencia_source, fake_fetcher, now
    ):
        resultado = collect(
            collect_config,
            sources=(infomoney_source, agencia_source),
            now=now,
            fetcher=fake_fetcher,
        )
        assert len(resultado.items) > 0
        assert not resultado.errors

    def test_deduplica_entre_fontes(
        self, collect_config, infomoney_source, agencia_source, fake_fetcher, now
    ):
        resultado = collect(
            collect_config,
            sources=(infomoney_source, agencia_source),
            now=now,
            fetcher=fake_fetcher,
        )
        # A noticia do Copom aparece nos dois feeds com manchetes parecidas.
        copom = [i for i in resultado.items if "Copom" in i.title]
        assert len(copom) == 1
        assert resultado.stats["_duplicatas_descartadas"] >= 1

    def test_feed_com_erro_nao_aborta_os_demais(
        self, collect_config, infomoney_source, fake_fetcher, now
    ):
        from podcast.sources import FeedSource

        quebrado = FeedSource(key="fora_do_ar", name="Fora", url="https://x.invalido/f")
        resultado = collect(
            collect_config,
            sources=(infomoney_source, quebrado),
            now=now,
            fetcher=fake_fetcher,
        )
        assert len(resultado.errors) == 1
        assert resultado.errors[0].source_key == "fora_do_ar"
        assert len(resultado.items) > 0  # o InfoMoney continuou

    def test_respeita_janela_de_tempo(
        self, collect_config, infomoney_source, fake_fetcher, now
    ):
        resultado = collect(
            collect_config,
            sources=(infomoney_source,),
            now=now,
            fetcher=fake_fetcher,
        )
        assert not any("antiga" in i.title for i in resultado.items)

    def test_respeita_teto_por_feed(
        self, infomoney_source, fake_fetcher, now
    ):
        from podcast.config import CollectConfig

        config = CollectConfig(window_hours=24, timeout=5, max_items_per_feed=1)
        resultado = collect(
            config, sources=(infomoney_source,), now=now, fetcher=fake_fetcher
        )
        assert len(resultado.items) == 1

    def test_seen_store_filtra_execucao_seguinte(
        self, collect_config, infomoney_source, fake_fetcher, now, tmp_path
    ):
        store = SeenStore(tmp_path / "seen.json").load()

        primeira = collect(
            collect_config, sources=(infomoney_source,), seen=store,
            now=now, fetcher=fake_fetcher,
        )
        assert len(primeira.items) > 0
        store.mark(primeira.items)

        segunda = collect(
            collect_config, sources=(infomoney_source,), seen=store,
            now=now, fetcher=fake_fetcher,
        )
        assert segunda.items == []

    def test_estatisticas_preenchidas(
        self, collect_config, infomoney_source, fake_fetcher, now
    ):
        resultado = collect(
            collect_config, sources=(infomoney_source,), now=now, fetcher=fake_fetcher
        )
        assert resultado.stats["_total"] == len(resultado.items)
        assert "infomoney_mercados" in resultado.stats


class TestPersistencia:
    def test_grava_e_le_de_volta(
        self, collect_config, infomoney_source, fake_fetcher, now, tmp_path
    ):
        original = collect(
            collect_config, sources=(infomoney_source,), now=now, fetcher=fake_fetcher
        )
        caminho = save_collection(original, tmp_path)
        assert caminho.name == "2026-08-03.json"

        recarregado = load_collection(caminho)
        assert len(recarregado.items) == len(original.items)
        assert recarregado.items[0].id == original.items[0].id
        assert recarregado.items[0].published_at == original.items[0].published_at

    def test_json_e_utf8_legivel(
        self, collect_config, agencia_source, fake_fetcher, now, tmp_path
    ):
        resultado = collect(
            collect_config, sources=(agencia_source,), now=now, fetcher=fake_fetcher
        )
        caminho = save_collection(resultado, tmp_path)
        # Acentos gravados literalmente, nao como \uXXXX
        assert "Agência" in caminho.read_text(encoding="utf-8")
