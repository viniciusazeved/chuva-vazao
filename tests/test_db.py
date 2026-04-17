"""Smoke tests do acesso ao SQLite."""
from __future__ import annotations

import pytest

from chuva_vazao import db


def test_list_estados_tem_26_ufs():
    estados = db.list_estados()
    assert len(estados) == 26
    assert "RJ" in estados
    assert "SP" in estados
    assert "DF" not in estados  # HidroFlu nao inclui DF


def test_list_postos_rj_tem_29():
    """RJ tem 8 postos IDF classicos + 21 Pfafstetter = 29."""
    postos = db.list_postos(estado="RJ")
    assert len(postos) == 29
    assert set(postos["fonte"]) == {"idf", "pfafstetter"}


def test_list_postos_por_fonte():
    idf_only = db.list_postos(fonte="idf")
    pfaf_only = db.list_postos(fonte="pfafstetter")
    assert len(idf_only) == 8
    assert len(pfaf_only) == 98
    assert (idf_only["estado"] == "RJ").all()


def test_get_idf_coef_santa_cruz():
    coef = db.get_idf_coef("Santa Cruz")
    assert coef is not None
    assert coef.estado == "RJ"
    assert coef.K == pytest.approx(711.3)
    assert coef.a == pytest.approx(0.186)
    assert coef.b == pytest.approx(0.687)
    assert coef.c == pytest.approx(7.0)


def test_get_idf_coef_inexistente():
    assert db.get_idf_coef("Cidade Fantasma") is None


def test_get_pfafstetter_coef_pcaxv():
    """Acentos preservados: Pça.XV deve ser consultavel."""
    coef = db.get_pfafstetter_coef("Rio de Janeiro - Pça.XV")
    assert coef is not None
    assert coef.estado == "RJ"
    assert coef.a > 0
    assert coef.b > 0


def test_contagem_por_estado():
    df = db.contagem_por_estado(fonte="pfafstetter")
    assert int(df["n"].sum()) == 98
    assert df.iloc[0]["estado"] == "RJ"  # RJ tem mais postos Pfafstetter
    assert int(df.iloc[0]["n"]) == 21
