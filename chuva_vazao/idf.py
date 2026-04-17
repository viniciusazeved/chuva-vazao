"""
Calculo de intensidades IDF a partir de coeficientes regionais.

Duas convencoes suportadas:

- "hidroflu" (default): i = K * TR^a / (t + c)^b
  Convencao do banco HidroFlu v2.0: b e o expoente da duracao e c e a constante
  temporal em minutos. Valida empiricamente contra os 8 postos IDF do RJ (K ~ 700-1500,
  a ~ 0.15-0.19, b ~ 0.66-0.80, c ~ 7-25).

- "idf_generator": i = K * TR^a / (t + b)^c
  Convencao usada no IDF-generator (D:/Projetos/IDF/idf.py:_idf_equation): aqui
  b e a constante e c e o expoente. Permite consumir CSVs exportados por aquela app.

A funcao `calcular_idf` recebe coeficientes nomeados explicitamente (K, expoente_tr,
expoente_duracao, constante_duracao) para eliminar ambiguidade. Adapters para as
convencoes classicas estao abaixo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

import numpy as np
import pandas as pd


Convention = Literal["hidroflu", "idf_generator"]


@dataclass(frozen=True)
class IDFParams:
    """Parametros IDF em convencao normalizada."""
    K: float
    expoente_tr: float
    expoente_duracao: float
    constante_duracao: float
    fonte: str = ""

    def intensidade(self, TR: float, duracao_min: float) -> float:
        """i = K * TR^a / (t + c)^b onde a=expoente_tr, b=expoente_duracao, c=constante."""
        return self.K * (TR ** self.expoente_tr) / (
            (duracao_min + self.constante_duracao) ** self.expoente_duracao
        )


def _from_hidroflu(K: float, a: float, b: float, c: float) -> IDFParams:
    """HidroFlu: a=expoente TR, b=expoente duracao, c=constante duracao."""
    return IDFParams(K=K, expoente_tr=a, expoente_duracao=b, constante_duracao=c)


def _from_idf_generator(K: float, a: float, b: float, c: float) -> IDFParams:
    """IDF-generator: a=expoente TR, b=constante duracao, c=expoente duracao."""
    return IDFParams(K=K, expoente_tr=a, expoente_duracao=c, constante_duracao=b)


def params_from_convention(
    K: float, a: float, b: float, c: float,
    convention: Convention = "hidroflu",
) -> IDFParams:
    """Cria IDFParams a partir de (K, a, b, c) na convencao indicada."""
    if convention == "hidroflu":
        return _from_hidroflu(K, a, b, c)
    if convention == "idf_generator":
        return _from_idf_generator(K, a, b, c)
    raise ValueError(f"convencao invalida: {convention!r}")


def calcular_idf(
    params: IDFParams,
    duracoes_min: Iterable[float],
    TRs: Iterable[float],
) -> pd.DataFrame:
    """
    Tabela de intensidades (mm/h) para todas as combinacoes TR x duracao.

    Returns
    -------
    pd.DataFrame
        Index = duracao (min), colunas = TR (anos), valores = intensidade (mm/h).
    """
    duracoes_arr = np.asarray(list(duracoes_min), dtype=float)
    trs_arr = np.asarray(list(TRs), dtype=float)

    resultado = {}
    for tr in trs_arr:
        resultado[tr] = [params.intensidade(tr, t) for t in duracoes_arr]

    df = pd.DataFrame(resultado, index=duracoes_arr)
    df.index = df.index.astype(float)
    df.index.name = "Duracao (min)"
    df.columns = [int(tr) if float(tr).is_integer() else float(tr) for tr in df.columns]
    df.columns.name = "TR (anos)"
    return df


def intensidade(
    params: IDFParams,
    TR: float,
    duracao_min: float,
) -> float:
    """Atalho: intensidade pontual (mm/h) para um par (TR, duracao)."""
    return params.intensidade(TR, duracao_min)


def altura_mm(
    params: IDFParams,
    TR: float,
    duracao_min: float,
) -> float:
    """Altura de chuva (mm) para a duracao indicada: h = i * t_horas."""
    return params.intensidade(TR, duracao_min) * duracao_min / 60.0


# ---------------------------------------------------------------------------
# Integracao com arquivos exportados pelo IDF-generator
# ---------------------------------------------------------------------------

import re


def params_from_idf_generator_txt(content: str) -> IDFParams:
    """
    Parseia o TXT exportado pelo IDF-generator.

    Formato esperado (linhas com 'K = 123.45', 'a = 0.2', etc):
        Equacao IDF: i = K * TR^a / (t + b)^c
        K = ...
        a = ...
        b = ...
        c = ...

    Os parametros vem na convencao idf_generator (b=constante, c=expoente).
    """
    def grab(chave: str) -> float:
        m = re.search(rf"\b{chave}\s*=\s*([-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)", content)
        if not m:
            raise ValueError(f"Nao achei '{chave} = <numero>' no arquivo.")
        return float(m.group(1))

    return _from_idf_generator(grab("K"), grab("a"), grab("b"), grab("c"))


def params_from_idf_generator_csv(path_or_content: str) -> IDFParams:
    """
    Parseia CSV do IDF-generator em dois formatos possiveis:

    (1) CSV de parametros — colunas K, a, b, c (convencao idf_generator).
    (2) CSV de tabela — primeira coluna = duracao em minutos, demais colunas =
        TRs em anos. Valores = intensidade (mm/h). Ajuste `K, a, b, c` e feito
        por regressao nao-linear na equacao `i = K * TR^a / (t + b)^c`.

    Aceita path de arquivo ou conteudo string.
    """
    import io as _io
    from pathlib import Path

    src = path_or_content
    if len(src) < 500 and Path(src).exists():
        df = pd.read_csv(src)
    else:
        df = pd.read_csv(_io.StringIO(src))

    lower = {c.lower(): c for c in df.columns}

    # Formato 1: colunas K, a, b, c
    if all(k in lower for k in ("k", "a", "b", "c")):
        row = df.iloc[0]
        K = float(row[lower["k"]])
        a = float(row[lower["a"]])
        b = float(row[lower["b"]])
        c = float(row[lower["c"]])
        return _from_idf_generator(K, a, b, c)

    # Formato 2: tabela (duracao, TR1, TR2, ...)
    return _fit_params_from_table(df)


def _fit_params_from_table(df: pd.DataFrame) -> IDFParams:
    """
    Ajusta `i = K * TR^a / (t + b)^c` por regressao nao-linear sobre a tabela.

    Espera DataFrame com a 1a coluna = duracao (min) e as demais = TRs (anos).
    Os valores da celula sao a intensidade (mm/h).
    """
    from scipy.optimize import curve_fit

    # Primeira coluna = duracao (nome tipo "Duracao (min)" ou "Duração (min)")
    duracao_col = df.columns[0]
    durs = df[duracao_col].astype(float).values

    # Demais colunas = TRs (nomes numericos "2", "5", "10", ...)
    tr_cols = df.columns[1:]
    try:
        trs = np.array([float(c) for c in tr_cols])
    except ValueError as exc:
        raise ValueError(
            f"CSV nao e tabela IDF-generator valida. Colunas esperadas: "
            f"primeira = duracao, demais = TRs numericos. Colunas recebidas: "
            f"{list(df.columns)}"
        ) from exc

    # Empilha (tr, dur, i)
    tr_grid, dur_grid = np.meshgrid(trs, durs)
    i_grid = df[tr_cols].astype(float).values
    tr_flat = tr_grid.ravel()
    dur_flat = dur_grid.ravel()
    i_flat = i_grid.ravel()

    # Remove NaNs se houver
    valid = np.isfinite(i_flat) & (i_flat > 0)
    tr_flat = tr_flat[valid]
    dur_flat = dur_flat[valid]
    i_flat = i_flat[valid]
    if len(i_flat) < 8:
        raise ValueError(
            f"Poucos pontos validos na tabela ({len(i_flat)}). "
            "Precisa de pelo menos 8 para ajustar K, a, b, c."
        )

    def _model(X, K, a, b, c):
        tr, dur = X
        return K * (tr ** a) / ((dur + b) ** c)

    # Chute inicial em faixa tipica (HidroFlu RJ: K~1000, a~0.17, b~10, c~0.75)
    p0 = [1000.0, 0.17, 10.0, 0.75]
    bounds = ([1.0, 0.05, 0.0, 0.3], [1e5, 1.0, 120.0, 1.5])
    try:
        popt, _ = curve_fit(
            _model, (tr_flat, dur_flat), i_flat,
            p0=p0, bounds=bounds, maxfev=10000,
        )
    except Exception as exc:
        raise ValueError(f"Ajuste K, a, b, c nao convergiu: {exc}") from exc

    K_fit, a_fit, b_fit, c_fit = (float(x) for x in popt)
    # RMSE relativo pra sanity
    i_pred = _model((tr_flat, dur_flat), *popt)
    rmse_rel = float(np.sqrt(np.mean(((i_pred - i_flat) / i_flat) ** 2)))
    fonte = (
        f"Ajuste IDF-generator (tabela, RMSE rel = {rmse_rel:.3%}): "
        f"K={K_fit:.1f}, a={a_fit:.3f}, b={b_fit:.2f}, c={c_fit:.3f}"
    )
    return IDFParams(
        K=K_fit,
        expoente_tr=a_fit,
        expoente_duracao=c_fit,  # convencao idf_generator: c=expoente
        constante_duracao=b_fit,  # b=constante
        fonte=fonte,
    )


def params_from_idf_generator_auto(filename: str, content: str | bytes) -> IDFParams:
    """
    Heuristica: usa extensao + conteudo para decidir entre TXT e CSV.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "txt" or "K =" in content[:500] or "K=" in content[:500]:
        return params_from_idf_generator_txt(content)
    return params_from_idf_generator_csv(content)
