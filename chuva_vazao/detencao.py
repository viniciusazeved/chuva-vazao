"""
Reservatorio de detencao: curvas S(h), O(h) e roteamento por Puls modificado.

Dispositivos combinados:
- Orificio de fundo: Q = Cd * A * sqrt(2 * g * h_eff),  h_eff = max(h - z_orificio, 0)
- Vertedor retangular de borda delgada: Q = Cw * b * h_over_weir^(3/2)

Metodo de Puls modificado (Chow, Maidment & Mays, 1988 cap.8):
    (I1 + I2)/2 - (O1 + O2)/2 = (S2 - S1) / dt
    => 2*S2/dt + O2 = (I1 + I2) + (2*S1/dt - O1)
    Pre-computa phi(h) = 2*S(h)/dt + O(h) e interpola h a partir do RHS.

Referencias:
- Chow, V. T., Maidment, D. R., & Mays, L. W. (1988). Applied Hydrology, cap. 8.
- Tucci, C. E. M. (2007). Hidrologia: Ciencia e Aplicacao, cap. 15.
- DAEE-SP. Manual de Calculo para Reservatorios de Detencao.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


G = 9.81  # m/s^2


# ---------------------------------------------------------------------------
# Dispositivos de saida
# ---------------------------------------------------------------------------

def orificio(Cd: float, A_m2: float, h_eff_m: float) -> float:
    """
    Q = Cd * A * sqrt(2 * g * h_eff).
    Retorna 0 se h_eff <= 0.
    """
    if h_eff_m <= 0:
        return 0.0
    return Cd * A_m2 * np.sqrt(2.0 * G * h_eff_m)


def vertedor_retangular(Cw: float, b_m: float, h_over_weir_m: float) -> float:
    """
    Q = Cw * b * h^(3/2).  Cw tipico = 1.85 (borda delgada, sem contracao).
    Retorna 0 se h_over_weir <= 0.
    """
    if h_over_weir_m <= 0:
        return 0.0
    return Cw * b_m * (h_over_weir_m ** 1.5)


# ---------------------------------------------------------------------------
# Curvas S(h) e O(h)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reservatorio:
    """Reservatorio prismatico com orificio de fundo + vertedor retangular."""
    Aw_m2: float                # area superficial (prismatico)
    h_max_m: float              # lamina maxima (topo do vertedor + borda livre)
    z_orificio_m: float         # cota do orificio (ref: fundo)
    d_orificio_m: float         # diametro do orificio
    z_vertedor_m: float         # cota do vertedor
    b_vertedor_m: float         # largura do vertedor
    Cd_orificio: float = 0.61
    Cw_vertedor: float = 1.85

    @property
    def A_orificio_m2(self) -> float:
        return np.pi * self.d_orificio_m ** 2 / 4.0

    def volume(self, h_m: float) -> float:
        """S(h) = Aw * h (prismatico)."""
        return self.Aw_m2 * max(h_m, 0.0)

    def vazao_saida(self, h_m: float) -> float:
        """O(h) = Q_orif(h) + Q_vert(h)."""
        q_orif = orificio(
            self.Cd_orificio,
            self.A_orificio_m2,
            max(h_m - self.z_orificio_m, 0.0),
        )
        q_vert = vertedor_retangular(
            self.Cw_vertedor,
            self.b_vertedor_m,
            max(h_m - self.z_vertedor_m, 0.0),
        )
        return q_orif + q_vert


def build_storage_discharge_table(
    reservatorio: Reservatorio,
    n_pontos: int = 200,
) -> pd.DataFrame:
    """
    Tabela h, S(h), O(h) densa para interpolacao.
    """
    h_vals = np.linspace(0.0, reservatorio.h_max_m, n_pontos)
    S_vals = np.array([reservatorio.volume(h) for h in h_vals])
    O_vals = np.array([reservatorio.vazao_saida(h) for h in h_vals])
    return pd.DataFrame({"h_m": h_vals, "S_m3": S_vals, "O_m3_s": O_vals})


# ---------------------------------------------------------------------------
# Puls modificado
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoteamentoResult:
    tempo_min: np.ndarray
    inflow_m3_s: np.ndarray
    outflow_m3_s: np.ndarray
    h_m: np.ndarray
    S_m3: np.ndarray

    @property
    def Qp_in_m3_s(self) -> float:
        return float(self.inflow_m3_s.max())

    @property
    def Qp_out_m3_s(self) -> float:
        return float(self.outflow_m3_s.max())

    @property
    def atenuacao_pct(self) -> float:
        if self.Qp_in_m3_s <= 0:
            return 0.0
        return 100.0 * (1.0 - self.Qp_out_m3_s / self.Qp_in_m3_s)

    @property
    def h_max_m(self) -> float:
        return float(self.h_m.max())

    @property
    def volume_armazenado_max_m3(self) -> float:
        return float(self.S_m3.max())

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            "tempo_min": self.tempo_min,
            "inflow_m3_s": self.inflow_m3_s,
            "outflow_m3_s": self.outflow_m3_s,
            "h_m": self.h_m,
            "S_m3": self.S_m3,
        })


def puls_routing(
    inflow_m3_s: Iterable[float],
    dt_min: float,
    reservatorio: Reservatorio,
    h_inicial_m: float = 0.0,
    n_pontos_tabela: int = 400,
) -> RoteamentoResult:
    """
    Roteia o hidrograma afluente atraves do reservatorio pelo Puls modificado.

    Para cada passo k:
        RHS = (I_k + I_{k+1}) + (2*S_k/dt - O_k)
        phi(h) = 2*S(h)/dt + O(h)
        Encontra h_{k+1} tal que phi(h_{k+1}) = RHS (interpolacao).

    Se RHS excede phi(h_max), o reservatorio extravasou: a lamina satura em
    h_max e uma warning e sinalizada (implicitamente O = O(h_max)).
    """
    inflow_arr = np.asarray(list(inflow_m3_s), dtype=float)
    dt_s = dt_min * 60.0
    n = len(inflow_arr)

    tabela = build_storage_discharge_table(reservatorio, n_pontos=n_pontos_tabela)
    h_vals = tabela["h_m"].to_numpy()
    S_vals = tabela["S_m3"].to_numpy()
    O_vals = tabela["O_m3_s"].to_numpy()
    phi_vals = 2.0 * S_vals / dt_s + O_vals

    h_series = np.zeros(n)
    S_series = np.zeros(n)
    O_series = np.zeros(n)

    h = h_inicial_m
    S = reservatorio.volume(h)
    O = reservatorio.vazao_saida(h)
    h_series[0] = h
    S_series[0] = S
    O_series[0] = O

    for k in range(n - 1):
        I1 = inflow_arr[k]
        I2 = inflow_arr[k + 1]
        RHS = (I1 + I2) + (2.0 * S / dt_s - O)

        # Interpolar h na curva phi
        if RHS >= phi_vals[-1]:
            h_new = h_vals[-1]  # satura no topo
        elif RHS <= phi_vals[0]:
            h_new = h_vals[0]
        else:
            h_new = float(np.interp(RHS, phi_vals, h_vals))

        S_new = reservatorio.volume(h_new)
        O_new = reservatorio.vazao_saida(h_new)

        h_series[k + 1] = h_new
        S_series[k + 1] = S_new
        O_series[k + 1] = O_new

        h, S, O = h_new, S_new, O_new

    tempo = np.arange(n) * dt_min
    return RoteamentoResult(
        tempo_min=tempo,
        inflow_m3_s=inflow_arr,
        outflow_m3_s=O_series,
        h_m=h_series,
        S_m3=S_series,
    )
