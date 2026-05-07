"""
Verificacao hidraulica de secao natural ou canalizada.

Cobre:
- Conversao de pontos topograficos 3D (N, E, Z) em secao 2D (estaca, cota).
- Geometria da secao molhada (A, P, B) para uma cota d'agua absoluta y_w.
- Manning aplicado a secao irregular: v, Q, Fr e regime.
- Lamina normal y_n via brentq dado Q de projeto.
- Lamina critica y_c via brentq em Fr = 1 (independente de n e S).
- Declividade media do trecho a partir dos thalwegs de 3 secoes.
- Verificacao consolidada (montante, central, jusante).

Convencoes:
- Coordenadas em CRS metrico (UTM/SIRGAS 2000). Lat/lon em graus dao
  distancia errada — o usuario e avisado na UI.
- Pontos de uma secao lancados na ordem do levantamento (margem esquerda
  para margem direita); estaca local cresce nessa ordem.
- Thalweg = ponto de menor Z; cota de fundo do trecho usa Z dos thalwegs.

Referencias:
- Chow, V. T. (1959). Open-Channel Hydraulics. McGraw-Hill (Tabela 5-6).
- Chaudhry, M. H. (2008). Open-Channel Flow. Springer, 2a ed.
- Porto, R. M. (2006). Hidraulica Basica. EESC-USP.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from scipy.optimize import brentq


# Aceleracao da gravidade (m/s^2)
G = 9.806


# ---------------------------------------------------------------------------
# Coeficientes n de Manning para canal natural (Chow 1959, Tabela 5-6,
# valores tipicos arredondados; expandir conforme necessario).
# ---------------------------------------------------------------------------

MANNING_N_NATURAL: dict[str, float] = {
    "Rio em planicie - limpo, reto, sem bancos": 0.030,
    "Rio em planicie - limpo, sinuoso, alguns pocos/bancos": 0.040,
    "Rio em planicie - sinuoso com matos e pedras": 0.050,
    "Rio em planicie - vegetacao densa nas margens": 0.075,
    "Curso d'agua de montanha - leito de cascalho": 0.040,
    "Curso d'agua de montanha - leito de matacoes": 0.050,
    "Canal escavado em terra - reto e limpo": 0.022,
    "Canal escavado em terra - com vegetacao curta": 0.027,
    "Canal escavado em terra - com matos e arbustos": 0.035,
    "Canal em rocha lisa": 0.025,
    "Canal em rocha irregular ou explodida": 0.040,
}


# ---------------------------------------------------------------------------
# Geometria 3D -> 2D
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PontoSecao:
    """Ponto topografico (N, E, Z) em metros (CRS metrico, ex: SIRGAS UTM)."""
    N: float
    E: float
    Z: float


@dataclass(frozen=True)
class SecaoTransversal:
    """
    Secao transversal 2D derivada de pontos topograficos 3D.

    Atributos:
        pontos_3d: pontos originais na ordem do levantamento (esquerda -> direita).
        estacas: posicao 2D acumulada (m) — primeiro ponto em estaca = 0.
        cotas: elevacoes Z absolutas (m) dos pontos.
        z_thalweg: Z minimo da secao (cota de fundo).
        N_thalweg, E_thalweg: coordenadas planimetricas do thalweg.
        z_max: Z maximo (margem mais alta) — limita a lamina antes de extravasar.
        largura_topo: comprimento total da secao (m).
    """
    pontos_3d: tuple[PontoSecao, ...]
    estacas: tuple[float, ...]
    cotas: tuple[float, ...]
    z_thalweg: float
    N_thalweg: float
    E_thalweg: float
    z_max: float
    largura_topo: float


def secao_from_pontos(pontos: Sequence[PontoSecao]) -> SecaoTransversal:
    """
    Constroi uma SecaoTransversal a partir de pontos topograficos.

    Estaca local = comprimento acumulado no plano horizontal (N, E), com 0 no
    primeiro ponto. Cota = Z absoluto. Thalweg = ponto de menor Z.
    """
    if len(pontos) < 3:
        raise ValueError(
            f"Secao precisa de >= 3 pontos para definir o leito; recebi {len(pontos)}."
        )

    estacas = [0.0]
    for i in range(1, len(pontos)):
        dN = pontos[i].N - pontos[i - 1].N
        dE = pontos[i].E - pontos[i - 1].E
        dist = math.hypot(dN, dE)
        if dist <= 0:
            raise ValueError(
                f"Pontos {i - 1} e {i} tem coordenadas N,E iguais — "
                f"distancia zero entre eles."
            )
        estacas.append(estacas[-1] + dist)

    cotas = [p.Z for p in pontos]
    idx_min = min(range(len(cotas)), key=lambda i: cotas[i])

    return SecaoTransversal(
        pontos_3d=tuple(pontos),
        estacas=tuple(estacas),
        cotas=tuple(cotas),
        z_thalweg=cotas[idx_min],
        N_thalweg=pontos[idx_min].N,
        E_thalweg=pontos[idx_min].E,
        z_max=max(cotas),
        largura_topo=estacas[-1],
    )


# ---------------------------------------------------------------------------
# Geometria molhada (A, P, B) para cota d'agua y_w
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeometriaMolhada:
    A_m2: float           # area molhada
    P_m: float            # perimetro molhado (so o leito; nao inclui o topo)
    B_m: float            # largura no espelho d'agua
    R_m: float            # raio hidraulico = A / P
    D_h_m: float          # profundidade hidraulica = A / B (Froude)


def geometria_molhada(secao: SecaoTransversal, y_w: float) -> GeometriaMolhada:
    """
    Calcula A, P, B numa secao irregular para uma cota d'agua absoluta y_w.

    Caminha pelos segmentos consecutivos (i, i+1) somando contribuicoes:
    - ambos > y_w: segmento seco, ignora.
    - ambos <= y_w: trapezio molhado completo.
    - parcial: corta em z = y_w por interpolacao linear.

    Trata naturalmente secoes com multiplas regioes molhadas (rio com ilha).
    """
    if y_w <= secao.z_thalweg:
        return GeometriaMolhada(A_m2=0.0, P_m=0.0, B_m=0.0, R_m=0.0, D_h_m=0.0)

    A = 0.0
    P = 0.0
    B = 0.0
    s = secao.estacas
    z = secao.cotas

    for i in range(len(s) - 1):
        s0, z0 = s[i], z[i]
        s1, z1 = s[i + 1], z[i + 1]
        h0 = y_w - z0
        h1 = y_w - z1

        if h0 <= 0 and h1 <= 0:
            continue

        if h0 > 0 and h1 > 0:
            largura = s1 - s0
            A += 0.5 * (h0 + h1) * largura
            P += math.hypot(s1 - s0, z1 - z0)
            B += largura
            continue

        # Parcial: encontrar interseccao com a horizontal y_w
        # z(t) = z0 + t (z1 - z0); resolver z(t*) = y_w
        if z1 != z0:
            t_star = (y_w - z0) / (z1 - z0)
        else:
            t_star = 0.0  # segmento horizontal degenerado (nao deveria acontecer aqui)
        t_star = max(0.0, min(1.0, t_star))
        s_star = s0 + t_star * (s1 - s0)

        if h0 > 0:
            # Esquerda submersa, direita emersa
            largura = s_star - s0
            A += 0.5 * h0 * largura
            P += math.hypot(s_star - s0, y_w - z0)
            B += largura
        else:
            # Esquerda emersa, direita submersa
            largura = s1 - s_star
            A += 0.5 * h1 * largura
            P += math.hypot(s1 - s_star, z1 - y_w)
            B += largura

    R = A / P if P > 0 else 0.0
    D_h = A / B if B > 0 else 0.0
    return GeometriaMolhada(A_m2=A, P_m=P, B_m=B, R_m=R, D_h_m=D_h)


# ---------------------------------------------------------------------------
# Manning aplicado a secao irregular
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EscoamentoSecao:
    y_w_m: float          # cota d'agua absoluta
    y_lamina_m: float     # lamina sobre o thalweg = y_w - z_thalweg
    A_m2: float
    P_m: float
    B_m: float
    R_m: float
    D_h_m: float
    v_m_s: float
    Q_m3_s: float
    Fr: float
    regime: str           # "subcritico" | "critico" | "supercritico" | "seco"
    n: float
    S_m_per_m: float


def _classifica_regime(Fr: float) -> str:
    if Fr <= 0:
        return "seco"
    if Fr < 0.95:
        return "subcritico"
    if Fr <= 1.05:
        return "critico"
    return "supercritico"


def manning_secao(
    secao: SecaoTransversal, y_w: float, n: float, S: float,
) -> EscoamentoSecao:
    """
    Manning para uma cota d'agua y_w numa secao irregular.

    Q = (1/n) * A * R^(2/3) * S^(1/2)
    Fr = v / sqrt(g * D_h), D_h = A / B
    """
    if n <= 0:
        raise ValueError(f"n de Manning precisa ser > 0, recebi {n}.")
    if S <= 0:
        raise ValueError(f"Declividade S precisa ser > 0, recebi {S}.")

    g = geometria_molhada(secao, y_w)
    if g.A_m2 <= 0:
        return EscoamentoSecao(
            y_w_m=y_w, y_lamina_m=0.0, A_m2=0.0, P_m=0.0, B_m=0.0,
            R_m=0.0, D_h_m=0.0, v_m_s=0.0, Q_m3_s=0.0, Fr=0.0,
            regime="seco", n=n, S_m_per_m=S,
        )
    v = (1.0 / n) * (g.R_m ** (2.0 / 3.0)) * math.sqrt(S)
    Q = v * g.A_m2
    Fr = v / math.sqrt(G * g.D_h_m) if g.D_h_m > 0 else 0.0
    return EscoamentoSecao(
        y_w_m=y_w, y_lamina_m=y_w - secao.z_thalweg,
        A_m2=g.A_m2, P_m=g.P_m, B_m=g.B_m, R_m=g.R_m, D_h_m=g.D_h_m,
        v_m_s=v, Q_m3_s=Q, Fr=Fr, regime=_classifica_regime(Fr),
        n=n, S_m_per_m=S,
    )


def lamina_normal(
    secao: SecaoTransversal, Q_target: float, n: float, S: float,
) -> EscoamentoSecao:
    """
    Encontra y_w tal que Q_manning(y_w; n, S) = Q_target.

    Faixa de busca: thalweg + eps -> z_max - eps. Lanca se Q_target excede a
    capacidade da secao na cota de extravasamento (margem mais baixa).
    """
    if Q_target <= 0:
        raise ValueError(f"Q_target precisa ser > 0, recebi {Q_target}.")

    eps = 1e-4
    y_min = secao.z_thalweg + eps
    y_max = secao.z_max - eps

    Q_topo = manning_secao(secao, y_max, n, S).Q_m3_s
    if Q_topo < Q_target:
        raise ValueError(
            f"Q de projeto ({Q_target:.3f} m3/s) extravasa a secao: capacidade "
            f"maxima ~{Q_topo:.3f} m3/s na cota da margem mais baixa "
            f"({secao.z_max:.3f} m). Reveja a geometria ou aumente a secao."
        )

    def f(y):
        return manning_secao(secao, y, n, S).Q_m3_s - Q_target

    y_w_norm = brentq(f, y_min, y_max)
    return manning_secao(secao, y_w_norm, n, S)


def lamina_critica(secao: SecaoTransversal, Q: float) -> EscoamentoSecao:
    """
    Encontra y_w tal que Fr = 1, i.e. Q^2 * B / (g * A^3) = 1.

    Independente de n e S — propriedade puramente geometrica + cinematica.
    """
    if Q <= 0:
        raise ValueError(f"Q precisa ser > 0, recebi {Q}.")

    eps = 1e-4
    y_min = secao.z_thalweg + eps
    y_max = secao.z_max - eps

    def f(y):
        g = geometria_molhada(secao, y)
        if g.A_m2 <= 0 or g.B_m <= 0:
            return 1.0  # Fr -> infinito quando A,B -> 0; forca sinal positivo
        return Q * Q * g.B_m / (G * g.A_m2 ** 3) - 1.0

    f_lo = f(y_min)
    f_hi = f(y_max)
    if f_lo * f_hi > 0:
        raise ValueError(
            f"Lamina critica para Q={Q:.3f} m3/s fora da faixa "
            f"[{secao.z_thalweg:.3f}, {secao.z_max:.3f}] m da secao. "
            f"Verifique se Q e a geometria sao compativeis."
        )

    y_c = brentq(f, y_min, y_max)
    g = geometria_molhada(secao, y_c)
    v_c = Q / g.A_m2 if g.A_m2 > 0 else 0.0
    return EscoamentoSecao(
        y_w_m=y_c, y_lamina_m=y_c - secao.z_thalweg,
        A_m2=g.A_m2, P_m=g.P_m, B_m=g.B_m, R_m=g.R_m, D_h_m=g.D_h_m,
        v_m_s=v_c, Q_m3_s=Q, Fr=1.0, regime="critico",
        n=float("nan"), S_m_per_m=float("nan"),
    )


# ---------------------------------------------------------------------------
# Trecho com 3 secoes consecutivas (M -> C -> J)
# ---------------------------------------------------------------------------

def declividade_trecho(
    sec_M: SecaoTransversal, sec_C: SecaoTransversal, sec_J: SecaoTransversal,
) -> tuple[float, float, float]:
    """
    Distancias planimetricas entre thalwegs e declividade media do trecho.

    Retorna (L_MC, L_CJ, S). Lanca se cota cresce de montante para jusante
    (entrada na ordem errada) ou se distancia entre thalwegs e zero.
    """
    L_MC = math.hypot(
        sec_C.N_thalweg - sec_M.N_thalweg,
        sec_C.E_thalweg - sec_M.E_thalweg,
    )
    L_CJ = math.hypot(
        sec_J.N_thalweg - sec_C.N_thalweg,
        sec_J.E_thalweg - sec_C.E_thalweg,
    )
    L_total = L_MC + L_CJ
    if L_total <= 0:
        raise ValueError(
            "Distancia total entre thalwegs e zero — coordenadas das tres "
            "secoes coincidem?"
        )
    dz = sec_M.z_thalweg - sec_J.z_thalweg
    if dz <= 0:
        raise ValueError(
            f"Cota do thalweg jusante ({sec_J.z_thalweg:.3f} m) >= montante "
            f"({sec_M.z_thalweg:.3f} m). Verifique a ordem das secoes."
        )
    return L_MC, L_CJ, dz / L_total


@dataclass(frozen=True)
class VerificacaoTrecho:
    """Verificacao consolidada de um trecho com 3 secoes M -> C -> J."""
    secao_montante: SecaoTransversal
    secao_central: SecaoTransversal
    secao_jusante: SecaoTransversal
    L_MC_m: float
    L_CJ_m: float
    L_total_m: float
    S_trecho_m_per_m: float
    Q_projeto_m3_s: float
    n: float
    escoamento_M: EscoamentoSecao
    escoamento_C: EscoamentoSecao
    escoamento_J: EscoamentoSecao
    critica_M: EscoamentoSecao
    critica_C: EscoamentoSecao
    critica_J: EscoamentoSecao
    warnings: list[str] = field(default_factory=list)


def verificar_trecho(
    sec_M: SecaoTransversal,
    sec_C: SecaoTransversal,
    sec_J: SecaoTransversal,
    Q_projeto: float,
    n: float,
    borda_livre_min_m: float = 0.30,
) -> VerificacaoTrecho:
    """
    Verificacao em regime uniforme nas 3 secoes do trecho com a mesma S.

    Calcula a declividade media via thalwegs, depois roda Manning em cada
    secao para obter a lamina normal e a critica. Gera warnings de borda
    livre insuficiente e regime supercritico.
    """
    L_MC, L_CJ, S = declividade_trecho(sec_M, sec_C, sec_J)

    esc_M = lamina_normal(sec_M, Q_projeto, n, S)
    esc_C = lamina_normal(sec_C, Q_projeto, n, S)
    esc_J = lamina_normal(sec_J, Q_projeto, n, S)
    crit_M = lamina_critica(sec_M, Q_projeto)
    crit_C = lamina_critica(sec_C, Q_projeto)
    crit_J = lamina_critica(sec_J, Q_projeto)

    warnings: list[str] = []
    for nome, esc, sec in [
        ("Montante", esc_M, sec_M),
        ("Central", esc_C, sec_C),
        ("Jusante", esc_J, sec_J),
    ]:
        borda = sec.z_max - esc.y_w_m
        if borda < borda_livre_min_m:
            warnings.append(
                f"Borda livre na secao {nome} = {borda:.2f} m "
                f"(< {borda_livre_min_m:.2f} m recomendado)."
            )
        if esc.Fr > 1.05:
            warnings.append(
                f"Regime supercritico (Fr={esc.Fr:.2f}) na secao {nome}: "
                f"avaliar protecao contra erosao do leito."
            )

    return VerificacaoTrecho(
        secao_montante=sec_M, secao_central=sec_C, secao_jusante=sec_J,
        L_MC_m=L_MC, L_CJ_m=L_CJ, L_total_m=L_MC + L_CJ,
        S_trecho_m_per_m=S, Q_projeto_m3_s=Q_projeto, n=n,
        escoamento_M=esc_M, escoamento_C=esc_C, escoamento_J=esc_J,
        critica_M=crit_M, critica_C=crit_C, critica_J=crit_J,
        warnings=warnings,
    )
