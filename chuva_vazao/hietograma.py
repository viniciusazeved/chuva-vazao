"""
Geracao de hietogramas de projeto a partir de uma equacao IDF.

Dois metodos classicos:
- Blocos alternados (Chicago): distribui a chuva em blocos centralizados no pico,
  garantindo que qualquer duracao central corresponda a intensidade da IDF para
  aquela duracao.
- Huff (1967): curvas adimensionais por quartil da duracao do evento (1o a 4o),
  indicando onde se concentra o maximo da chuva.

Ambos retornam pandas.Series indexada por tempo (em minutos), com valores em
milimetros por passo `dt`.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from chuva_vazao.idf import IDFParams


HuffQuartil = Literal[1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Huff — curvas adimensionais (Huff, 1967 — Illinois State Water Survey)
# Tabelas classicas reproduzidas em Chow, Maidment & Mays (1988) e Tucci (2009).
# Cada lista dá a fracao acumulada da chuva total para fracao acumulada do tempo
# (0%, 10%, 20%, ..., 100%).
# ---------------------------------------------------------------------------

_HUFF_CUMULATIVE: dict[int, list[float]] = {
    1: [0.00, 0.18, 0.36, 0.52, 0.64, 0.74, 0.82, 0.87, 0.92, 0.96, 1.00],
    2: [0.00, 0.07, 0.17, 0.30, 0.45, 0.62, 0.76, 0.86, 0.92, 0.97, 1.00],
    3: [0.00, 0.04, 0.10, 0.17, 0.26, 0.38, 0.52, 0.68, 0.82, 0.93, 1.00],
    4: [0.00, 0.02, 0.06, 0.11, 0.18, 0.27, 0.38, 0.50, 0.64, 0.80, 1.00],
}


# ---------------------------------------------------------------------------
# Blocos alternados (Chicago)
# ---------------------------------------------------------------------------

def blocos_alternados(
    params: IDFParams,
    TR: float,
    duracao_total_min: float,
    dt_min: float,
) -> pd.Series:
    """
    Hietograma de projeto pelo metodo de blocos alternados.

    Algoritmo:
    1. Calcula alturas acumuladas h(t_i) = i(TR, t_i) * t_i / 60 para t_i = dt, 2*dt, ...
    2. Deriva alturas incrementais h_i = h(t_i) - h(t_{i-1}).
    3. Ordena alturas por magnitude decrescente.
    4. Redistribui em torno do pico central (blocos alternados).

    Returns
    -------
    pd.Series
        Index = tempo acumulado (minutos) no final de cada bloco,
        valores = altura de chuva no intervalo (mm).
    """
    if dt_min <= 0 or duracao_total_min <= 0:
        raise ValueError("dt_min e duracao_total_min devem ser positivos.")
    if duracao_total_min < dt_min:
        raise ValueError("duracao_total_min deve ser >= dt_min.")

    n_blocos = int(round(duracao_total_min / dt_min))
    duracao_total_min = n_blocos * dt_min

    # Alturas acumuladas para duracoes crescentes
    tempos_acum = np.arange(1, n_blocos + 1) * dt_min
    intensidades = np.array([params.intensidade(TR, t) for t in tempos_acum])
    alturas_acum = intensidades * tempos_acum / 60.0

    # Alturas incrementais
    alturas_inc = np.diff(alturas_acum, prepend=0.0)

    # Reorganizar: blocos alternados centralizados no pico
    ordem = np.argsort(alturas_inc)[::-1]  # descendente
    posicoes = _posicoes_blocos_alternados(n_blocos)
    hietograma = np.zeros(n_blocos)
    for rank, pos in enumerate(posicoes):
        hietograma[pos] = alturas_inc[ordem[rank]]

    tempos = np.arange(1, n_blocos + 1) * dt_min
    return pd.Series(hietograma, index=tempos, name="altura_mm").rename_axis("tempo_min")


def _posicoes_blocos_alternados(n: int) -> list[int]:
    """Indices [centro, centro-1, centro+1, centro-2, centro+2, ...]."""
    centro = n // 2
    posicoes = [centro]
    direcao = 1
    passo = 1
    while len(posicoes) < n:
        prox = centro + direcao * passo
        if 0 <= prox < n:
            posicoes.append(prox)
        if direcao == 1:
            direcao = -1
        else:
            direcao = 1
            passo += 1
    return posicoes[:n]


# ---------------------------------------------------------------------------
# Huff
# ---------------------------------------------------------------------------

def huff(
    params: IDFParams,
    TR: float,
    duracao_total_min: float,
    dt_min: float,
    quartil: HuffQuartil = 2,
) -> pd.Series:
    """
    Hietograma de projeto pelo metodo de Huff.

    Usa a intensidade IDF para a duracao total (logo a altura total e
    h_total = i(TR, D) * D / 60). Distribui essa altura segundo a curva
    adimensional do quartil escolhido.

    Parameters
    ----------
    params : IDFParams
    TR : float
    duracao_total_min : float
    dt_min : float
    quartil : 1 | 2 | 3 | 4
        Quartil em que se concentra o pico da chuva. 2 (segundo quartil) e
        comumente usado para chuvas curtas em bacias urbanas brasileiras.

    Returns
    -------
    pd.Series
        Index = tempo acumulado (min), valores = altura no intervalo (mm).
    """
    if quartil not in _HUFF_CUMULATIVE:
        raise ValueError(f"quartil deve ser 1..4 (recebi {quartil}).")

    n_blocos = int(round(duracao_total_min / dt_min))
    duracao_total_min = n_blocos * dt_min
    intensidade_media = params.intensidade(TR, duracao_total_min)
    h_total = intensidade_media * duracao_total_min / 60.0

    # Interpolacao da curva Huff (11 pontos, 0-100%)
    curva_x = np.linspace(0, 1, 11)
    curva_y = np.array(_HUFF_CUMULATIVE[quartil])

    tempos = np.arange(1, n_blocos + 1) * dt_min
    frac_t = tempos / duracao_total_min
    acum_adim = np.interp(frac_t, curva_x, curva_y)

    acum_h = acum_adim * h_total
    inc_h = np.diff(acum_h, prepend=0.0)

    return pd.Series(inc_h, index=tempos, name="altura_mm").rename_axis("tempo_min")


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------

def altura_total(hietograma: pd.Series) -> float:
    """Soma das alturas (mm) — verificacao de conservacao."""
    return float(hietograma.sum())


def intensidade_media(hietograma: pd.Series) -> float:
    """Intensidade media do hietograma (mm/h) = altura_total / duracao_horas."""
    duracao_h = hietograma.index.max() / 60.0
    return altura_total(hietograma) / duracao_h
