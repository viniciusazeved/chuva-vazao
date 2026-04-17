"""
Tempo de concentracao (tc) por formulas classicas.

Tres formulas independentes — util para bacias rurais e urbanas de pequeno a
medio porte (A < 500 km^2). Retornam tc em minutos.

Referencias:
- Kirpich (1940): Civil Engineering, 10(6), 362. Pennsylvania small watersheds.
- Ven Te Chow (1964): Handbook of Applied Hydrology.
- California Culverts Practice (1955): California Division of Highways.

Para bacias urbanizadas, aplique o multiplicador de Kerby ou use formulas
especificas (Eagleson, SCS lag). Este modulo assume drenagem natural.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True)
class TcResult:
    """Resultados das formulas de tc + media aritmetica."""
    kirpich_min: float
    ven_te_chow_min: float
    california_min: float

    @property
    def media_min(self) -> float:
        return (self.kirpich_min + self.ven_te_chow_min + self.california_min) / 3.0

    def to_dict(self) -> dict[str, float]:
        return {
            "Kirpich": self.kirpich_min,
            "Ven Te Chow": self.ven_te_chow_min,
            "California": self.california_min,
            "Media": self.media_min,
        }


def kirpich(L_km: float, S_m_per_m: float) -> float:
    """
    Kirpich (1940).
        tc [min] = 0.0195 * L_m^0.77 * S^(-0.385)

    Valido para bacias rurais pequenas (< 0.5 km^2 idealmente).
    Tende a subestimar tc em bacias maiores e em terrenos com coberturas
    retardantes (florestas).

    Parameters
    ----------
    L_km : float
        Comprimento do canal principal (km).
    S_m_per_m : float
        Declividade media do canal (adimensional, m/m).
    """
    if L_km <= 0 or S_m_per_m <= 0:
        raise ValueError("L_km e S_m_per_m devem ser positivos.")
    L_m = L_km * 1000.0
    return 0.0195 * (L_m ** 0.77) * (S_m_per_m ** -0.385)


def ven_te_chow(L_km: float, S_m_per_m: float) -> float:
    """
    Ven Te Chow (1964).
        tc [min] = 0.1602 * (L_km / sqrt(S))^0.64 * 60

    Modelo geral para bacias rurais ate medio porte (~50 km^2).
    """
    if L_km <= 0 or S_m_per_m <= 0:
        raise ValueError("L_km e S_m_per_m devem ser positivos.")
    return 0.1602 * ((L_km / sqrt(S_m_per_m)) ** 0.64) * 60.0


def california(L_km: float, H_m: float) -> float:
    """
    California Culverts Practice (1955).
        tc [min] = 57 * (L_km^3 / H_m)^0.385

    Recomendado para bacias montanhosas (H = desnivel ao longo do canal, m).
    """
    if L_km <= 0 or H_m <= 0:
        raise ValueError("L_km e H_m devem ser positivos.")
    return 57.0 * ((L_km ** 3) / H_m) ** 0.385


def tempo_concentracao_completo(L_km: float, H_m: float) -> TcResult:
    """
    Aplica as tres formulas e retorna TcResult.

    A declividade media S e derivada de H e L: S = H / L.

    Parameters
    ----------
    L_km : float
        Comprimento do canal principal (km).
    H_m : float
        Desnivel total ao longo do canal (m).
    """
    if L_km <= 0 or H_m <= 0:
        raise ValueError("L_km e H_m devem ser positivos.")
    S = H_m / (L_km * 1000.0)
    return TcResult(
        kirpich_min=kirpich(L_km, S),
        ven_te_chow_min=ven_te_chow(L_km, S),
        california_min=california(L_km, H_m),
    )
