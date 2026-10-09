"""Composição do texto (api/clima_texto.py) — sem rede e sem banco.

Os resumos são montados à mão, no formato de ``Resumo``. Nenhuma coordenada aparece aqui.
Os tamanhos de regional usados nos testes de redução foram medidos com o próprio módulo:
``A`` * n ocupa n bytes, e o texto sem reduções tem 106 + n bytes.
"""
import datetime
import logging

import pytest

from rastro_api.api import clima_texto as ct
from rastro_api.api.clima_previsao import Resumo
from rastro_api.api.clima_texto import LIMITE_BYTES, META_BYTES, compor

DIA = datetime.date(2026, 10, 9)


def _r(**kw) -> Resumo:
    base = dict(
        temp_min=22,
        temp_max=33,
        chance_chuva=70,
        periodo="à tarde",
        trovoada=False,
        vento_kmh=15,
        rajada_kmh=25,
        chuva_mm=0.0,
    )
    base.update(kw)
    return Resumo(**base)


def _tam(texto: str) -> int:
    return len(texto.encode("utf-8"))


def test_exemplo_aprovado_identico():
    r = _r(trovoada=True, chuva_mm=30.0)
    texto = compor("Médio Javari", DIA, "08h", r)
    assert texto == (
        "Médio Javari – 09/10 08h | Hoje: 22–33°C. Chuva à tarde, chance 70%. "
        "Trovoada provável. Vento fraco, rajadas 25 km/h. ALERTA: chuva forte"
    )
    assert _tam(texto) == 145


def test_sem_chuva_e_sem_trovoada():
    texto = compor("Itui", DIA, "08h", _r(chance_chuva=10, periodo=None))
    assert texto == (
        "Itui – 09/10 08h | Hoje: 22–33°C. Sem chuva prevista. Sem trovoada. "
        "Vento fraco, rajadas 25 km/h."
    )


@pytest.mark.parametrize(
    "periodo, chance, esperado",
    [
        ("de manhã", 45, "Chuva de manhã, chance 45%."),
        ("à noite", 20, "Chuva à noite, chance 20%."),
        ("o dia todo", 60, "Chuva o dia todo, chance 60%."),
    ],
)
def test_periodo_da_chuva(periodo, chance, esperado):
    texto = compor("Itui", DIA, "08h", _r(periodo=periodo, chance_chuva=chance))
    assert esperado in texto


def test_sem_alerta_termina_em_km_h():
    texto = compor("Itui", DIA, "08h", _r(chuva_mm=29.9, rajada_kmh=49))
    assert texto.endswith("Vento fraco, rajadas 49 km/h.")
    assert "ALERTA" not in texto


def test_alerta_so_ventania():
    texto = compor("Itui", DIA, "08h", _r(rajada_kmh=55, chuva_mm=5.0))
    assert texto.endswith("Vento fraco, rajadas 55 km/h. ALERTA: ventania")
    assert "chuva forte" not in texto


def test_alerta_so_chuva_forte():
    texto = compor("Itui", DIA, "08h", _r(chuva_mm=30.0))
    assert texto.endswith("ALERTA: chuva forte")
    assert "ventania" not in texto


def test_alerta_ambos():
    texto = compor("Itui", DIA, "08h", _r(chuva_mm=31.0, rajada_kmh=50))
    assert texto.endswith("ALERTA: chuva forte e ventania")


@pytest.mark.parametrize(
    "vento, classe",
    [(0, "fraco"), (19, "fraco"), (20, "moderado"), (40, "moderado"), (41, "forte")],
)
def test_classe_do_vento(vento, classe):
    texto = compor("Itui", DIA, "08h", _r(vento_kmh=vento))
    assert f"Vento {classe}, rajadas 25 km/h." in texto


def test_hora_meia_hora_no_texto():
    texto = compor("Itui", DIA, "08h30", _r())
    assert texto.startswith("Itui – 09/10 08h30 | ")


# Reduções. Com regional de n ``A`` e sem alerta, o texto tem 106 + n bytes.


def test_passo1_remove_sem_trovoada_e_para_quando_cabe():
    # n=100: 206 bytes com "Sem trovoada."; 192 sem ele, então os passos 2–4 não rodam.
    texto = compor("A" * 100, DIA, "08h", _r())
    assert "Sem trovoada" not in texto
    assert texto.startswith("A" * 100 + " – 09/10 08h | ")
    assert "Vento fraco, rajadas 25 km/h." in texto


def test_passo1_nao_remove_trovoada_real():
    texto = compor("A" * 100, DIA, "08h", _r(trovoada=True))
    # Passo 1 não muda nada (há trovoada); passos 2 e 3 rodam (207 bytes sem a hora, 193 com
    # vento curto); o passo 4 não roda porque já cabe.
    assert "Trovoada provável." in texto
    assert "08h" not in texto
    assert "Vento 25 km/h." in texto
    assert texto.startswith("A" * 100 + " – 09/10 | ")
    assert _tam(texto) == 193


def test_passo2_remove_hora_e_para_quando_cabe():
    # n=110: 202 sem trovoada e 198 sem a hora; o vento completo fica.
    texto = compor("A" * 110, DIA, "08h", _r())
    assert "08h" not in texto
    assert "Sem trovoada" not in texto
    assert "Vento fraco, rajadas 25 km/h." in texto
    assert texto.startswith("A" * 110 + " – 09/10 | ")


def test_passo3_vento_curto_e_para_quando_cabe():
    # n=120: 193 bytes com vento curto; o regional continua inteiro.
    texto = compor("A" * 120, DIA, "08h", _r())
    assert "Vento 25 km/h." in texto
    assert "fraco" not in texto
    assert texto.startswith("A" * 120 + " – ")


def test_passo4_regional_cortado_em_12():
    # n=130: 203 bytes mesmo com vento curto; cortando o regional em 12 caracteres cabe.
    texto = compor("A" * 130, DIA, "08h", _r())
    assert texto.startswith("A" * 12 + " – 09/10 | ")
    assert "Vento 25 km/h." in texto
    assert _tam(texto) <= LIMITE_BYTES


def test_regional_cortado_nao_termina_em_espaco():
    # Os 12 primeiros caracteres terminam em espaço; o corte não pode deixá-lo antes do " – ".
    texto = compor("Médio Javar " + "x" * 130, DIA, "08h", _r())
    assert texto.startswith("Médio Javar – ")


def test_resultado_nunca_passa_de_200_bytes_com_acentos():
    texto = compor("çí" * 200, DIA, "08h", _r(trovoada=True, chuva_mm=99.0, rajada_kmh=99))
    assert _tam(texto) <= LIMITE_BYTES
    assert texto.startswith("çíçíçíçíçíçí – ")
    texto.encode("utf-8").decode("utf-8")  # sem caractere quebrado


def test_corte_final_por_bytes_sem_quebrar_caractere(monkeypatch):
    # Sem os passos, o regional inteiro fica: o corte final tem de cair no meio do ``í``.
    monkeypatch.setattr(ct, "PASSOS", ())
    texto = compor("A" + "í" * 200, DIA, "08h", _r())
    assert _tam(texto) <= LIMITE_BYTES
    # 200 bytes = "A" + 99 ``í`` (199 bytes) + 1 byte de um 100º ``í``, que é descartado.
    assert texto == "A" + "í" * 99
    assert _tam(texto) == 199
    texto.encode("utf-8").decode("utf-8")


def test_aviso_acima_de_180_so_com_tamanho(caplog):
    with caplog.at_level(logging.WARNING, logger="rastro_api.clima"):
        texto = compor("A" * 75, DIA, "08h", _r())  # 181 bytes
    assert _tam(texto) == 181 > META_BYTES
    avisos = [r for r in caplog.records if r.name == "rastro_api.clima"]
    assert len(avisos) == 1
    assert avisos[0].levelno == logging.WARNING
    assert "181" in avisos[0].getMessage()
    assert "Hoje" not in avisos[0].getMessage()
    assert "Itui" not in avisos[0].getMessage()


def test_sem_aviso_em_180(caplog):
    with caplog.at_level(logging.WARNING, logger="rastro_api.clima"):
        texto = compor("A" * 74, DIA, "08h", _r())  # 180 bytes
    assert _tam(texto) == 180
    assert not [r for r in caplog.records if r.name == "rastro_api.clima"]
