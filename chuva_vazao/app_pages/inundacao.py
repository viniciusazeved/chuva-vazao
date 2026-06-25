"""Pagina 7: inundacao fluvial 1D (standard-step) sobre MDT.

Desenhe o eixo do rio -> o app gera secoes transversais perpendiculares
amostradas do MDT, resolve a linha d'agua por standard-step, projeta a mancha
de inundacao no terreno (profundidade = WSE - terreno) e a exibe sobre o relevo,
com perfil longitudinal, tabela por secao e export multi-formato (GeoPackage etc).
"""
from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from chuva_vazao import inundacao_1d as inu
from chuva_vazao import plots
from chuva_vazao import secao_natural as sn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _centro_lonlat(mdt_fonte: str) -> tuple[float, float]:
    """(lat, lon) do centro do MDT — abre so a metadata (leve ate p/ COG remoto)."""
    import rasterio  # noqa: PLC0415
    from rasterio.warp import transform as warp_transform  # noqa: PLC0415

    with rasterio.open(str(mdt_fonte)) as src:
        b = src.bounds
        xc = (b.left + b.right) / 2.0
        yc = (b.top + b.bottom) / 2.0
        crs = src.crs
    if crs is not None and crs.to_epsg() != 4326:
        lon, lat = warp_transform(crs, "EPSG:4326", [xc], [yc])
        return float(lat[0]), float(lon[0])
    return float(yc), float(xc)


def _fig_relevo_mancha(mancha) -> go.Figure:
    """Heatmap do relevo (MDT) com a profundidade da inundação sobreposta."""
    import numpy as np  # noqa: PLC0415

    z = mancha.mdt_z
    prof = mancha.profundidade
    nrow, ncol = z.shape
    step = max(1, int(max(nrow, ncol) // 700))   # subamostra p/ plot leve
    zz = z[::step, ::step]
    pp = prof[::step, ::step]
    t = mancha.transform
    xs = t.c + t.a * (np.arange(0, ncol, step) + 0.5)
    ys = t.f + t.e * (np.arange(0, nrow, step) + 0.5)

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=zz, x=xs, y=ys, colorscale="Greys", showscale=False,
        hovertemplate="E=%{x:.0f}<br>N=%{y:.0f}<br>z=%{z:.1f} m<extra></extra>",
    ))
    fig.add_trace(go.Heatmap(
        z=pp, x=xs, y=ys, colorscale="Blues", zmin=0.0, opacity=0.80,
        colorbar=dict(title="Prof. (m)"),
        hovertemplate="E=%{x:.0f}<br>N=%{y:.0f}<br>prof=%{z:.2f} m<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white", height=560,
        xaxis_title="E (m, UTM)", yaxis_title="N (m, UTM)",
        margin=dict(l=10, r=10, t=10, b=10),
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)  # aspecto 1:1 (georreferenciado)
    return fig


def _prof_rgba(mancha):
    """Array RGBA (uint8) da profundidade p/ ImageOverlay no folium. None se falhar."""
    try:
        import numpy as np  # noqa: PLC0415
        from matplotlib import cm  # noqa: PLC0415
        from matplotlib.colors import Normalize  # noqa: PLC0415

        prof = mancha.profundidade
        vmax = max(mancha.prof_max_m, 0.1)
        rgba = cm.get_cmap("Blues")(Normalize(0.0, vmax)(prof))
        rgba[..., 3] = np.where(np.isfinite(prof), 0.78, 0.0)
        return (rgba * 255).astype("uint8")
    except Exception:  # noqa: BLE001
        return None


def _mancha_shapefile_zip(mancha) -> bytes:
    """Empacota o contorno da mancha (EPSG:4326) como Shapefile zipado."""
    import io  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    import geopandas as gpd  # noqa: PLC0415
    from shapely.geometry import shape as shp_shape  # noqa: PLC0415

    fc = _json.loads(mancha.mancha_geojson)
    geoms = [shp_shape(f["geometry"]) for f in fc.get("features", [])]
    if not geoms:
        raise ValueError("mancha vazia")
    gdf = gpd.GeoDataFrame({"id": list(range(len(geoms)))}, geometry=geoms, crs="EPSG:4326")
    tmp = tempfile.mkdtemp()
    shp = os.path.join(tmp, "mancha_inundacao.shp")
    gdf.to_file(shp)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            f = shp.replace(".shp", ext)
            if os.path.exists(f):
                zf.write(f, os.path.basename(f))
    return buf.getvalue()


def _profundidade_geotiff(mancha) -> bytes:
    """GeoTIFF da profundidade (no CRS do MDT, nodata=-9999) p/ abrir no QGIS/ArcGIS."""
    import numpy as np  # noqa: PLC0415
    from rasterio.io import MemoryFile  # noqa: PLC0415

    prof = mancha.profundidade
    arr = np.where(np.isfinite(prof), prof, -9999.0).astype("float32")
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
            dtype="float32", crs=mancha.crs, transform=mancha.transform,
            nodata=-9999.0, compress="deflate", tiled=True,
        ) as dst:
            dst.write(arr, 1)
        return mem.read()


def _pacote_geopackage(ger, perfil, mancha) -> bytes:
    """GeoPackage multicamada (mancha + eixo + seções) em EPSG:4326 p/ ArcGIS/QGIS."""
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import geopandas as gpd  # noqa: PLC0415
    from pyproj import Transformer  # noqa: PLC0415
    from shapely.geometry import LineString  # noqa: PLC0415
    from shapely.geometry import shape as shp_shape  # noqa: PLC0415

    tr = Transformer.from_crs(ger.crs_trabalho, "EPSG:4326", always_xy=True)
    secoes = sorted(ger.secoes, key=lambda s: s.estaca_rio_m)
    wse = {round(p.estaca_rio_m, 3): p.wse_m for p in perfil.pontos}

    eixo = LineString([tr.transform(s.secao.E_thalweg, s.secao.N_thalweg) for s in secoes])
    gdf_eixo = gpd.GeoDataFrame({"nome": ["eixo do rio"]}, geometry=[eixo], crs="EPSG:4326")

    sec_geoms, sec_attr = [], []
    for s in secoes:
        sec_geoms.append(LineString([tr.transform(p.E, p.N) for p in s.secao.pontos_3d]))
        w = wse.get(round(s.estaca_rio_m, 3))
        sec_attr.append({
            "estaca_m": round(s.estaca_rio_m, 1),
            "wse_m": round(w, 2) if w is not None else None,
            "z_thalweg_m": round(s.secao.z_thalweg, 2),
        })
    gdf_sec = gpd.GeoDataFrame(sec_attr, geometry=sec_geoms, crs="EPSG:4326")

    fc = _json.loads(mancha.mancha_geojson)
    geoms = [shp_shape(f["geometry"]) for f in fc.get("features", [])]
    if not geoms:
        raise ValueError("mancha vazia")
    gdf_mancha = gpd.GeoDataFrame({"id": list(range(len(geoms)))}, geometry=geoms, crs="EPSG:4326")

    tmp = tempfile.mkdtemp()
    gpkg = os.path.join(tmp, "inundacao.gpkg")
    gdf_mancha.to_file(gpkg, layer="mancha", driver="GPKG")
    gdf_eixo.to_file(gpkg, layer="eixo", driver="GPKG")
    gdf_sec.to_file(gpkg, layer="secoes", driver="GPKG")
    with open(gpkg, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Cabecalho
# ---------------------------------------------------------------------------

st.title("7. Inundação fluvial 1D")
st.caption(
    "Desenhe o eixo do rio sobre o MDT. O app gera seções transversais "
    "perpendiculares, resolve a linha d'água por standard-step (escoamento "
    "gradualmente variado, subcrítico), projeta a mancha no terreno e a exibe "
    "sobre o relevo. Ideal: **MDT/terreno** — ou **topobatimétrico** (com a calha "
    "do rio), que dá a profundidade real do canal. Evite o MDS de superfície "
    "(telhado e copa viram dique falso)."
)

with st.expander("ℹ Como usar"):
    st.markdown(
        "- **Eixo do rio**: desenhe **1 polilinha** seguindo o talvegue, de "
        "**jusante para montante** (essa é a convenção; o sentido também é "
        "auto-detectado pelo thalweg). Quanto mais fiel ao canal, melhores as seções.\n"
        "- **MDT**: terreno ou **topobatimétrico** (com a calha do rio) — nunca o "
        "MDS de superfície.\n"
        "- **Largura das seções**: define até onde a mancha pode espraiar. Se vier "
        "aviso de *extravasamento*, **aumente a largura** (a lâmina passou do corredor).\n"
        "- **Espaçamento**: resolução longitudinal — menor = mais seções e mais "
        "detalhe (e mais lento).\n"
        "- **Q de projeto**: vem do pico do hidrograma (Página 3) quando você o "
        "rodou; senão, ajuste manual.\n"
        "- **Contorno de jusante**: *normal* (lâmina pela declividade do leito) na "
        "maioria dos casos; *cota* quando há nível conhecido a jusante (barramento, "
        "confluência, foz)."
    )


# ---------------------------------------------------------------------------
# 1. Fonte do MDT
# ---------------------------------------------------------------------------

st.subheader("1. Fonte do MDT")
modo = st.radio(
    "De onde vem o MDT?",
    ["Caminho local / URL COG (/vsicurl)", "Upload de GeoTIFF"],
    horizontal=True,
    key="inu_modo_mdt",
)

mdt_fonte: str | None = None
if modo.startswith("Caminho"):
    mdt_fonte = st.text_input(
        "Caminho local ou string VSI do COG",
        value=st.session_state.get("inu_mdt_fonte", ""),
        placeholder="D:/.../MDE_TRIBUTARIOS_V6.tif  ou  /vsicurl/https://bucket/mdt.tif",
        help="Aceita arquivo local ou COG remoto via GDAL VSI "
             "(/vsicurl/, /vsis3/, /vsigs/). A leitura e por janela — so os "
             "tiles da área desenhada sobem à RAM.",
    )
    if mdt_fonte:
        st.session_state["inu_mdt_fonte"] = mdt_fonte
else:
    up = st.file_uploader("MDT (.tif/.tiff)", type=["tif", "tiff"], key="inu_upload")
    if up is not None:
        cache = Path(st.session_state.get("_dem_cache_dir", "data/dems"))
        cache.mkdir(parents=True, exist_ok=True)
        p = cache / up.name
        p.write_bytes(up.read())
        mdt_fonte = str(p)
        st.caption(f"MDT salvo: `{p.name}`")

if not mdt_fonte:
    st.info("Informe um MDT para começar.")
    st.stop()

try:
    center = _centro_lonlat(mdt_fonte)
except Exception as exc:  # noqa: BLE001
    st.error(f"Não consegui abrir o MDT (`{mdt_fonte}`): {exc}")
    st.stop()


# ---------------------------------------------------------------------------
# 2. Vazao e rugosidade
# ---------------------------------------------------------------------------

st.subheader("2. Vazão e rugosidade")
hg = st.session_state.get("hidrograma")
try:
    q_default = float(hg["Q_m3s"].max()) if hg is not None else 50.0
except Exception:  # noqa: BLE001
    q_default = 50.0

c1, c2, c3 = st.columns(3)
with c1:
    Q = st.number_input(
        "Q de projeto (m³/s)", min_value=0.001, max_value=1e5,
        value=q_default, step=1.0, format="%.3f",
        help="Pico do hidrograma (Página 3) se houver; senão, manual.",
    )
with c2:
    material = st.selectbox(
        "Leito / rugosidade", list(sn.MANNING_N_NATURAL.keys()), index=1,
        help="Coeficiente n de Manning, Chow (1959) Tabela 5-6.",
    )
    n_manning = sn.MANNING_N_NATURAL[material]
    st.caption(f"n de Manning = {n_manning}")
with c3:
    cc = st.selectbox(
        "Contorno de jusante", ["normal", "critica", "cota"],
        help="normal = profundidade normal (Manning); critica = Fr=1; "
             "cota = cota d'água imposta.",
    )
    cota_jus = (
        st.number_input("Cota d'água de jusante (m)", value=0.0, format="%.2f")
        if cc == "cota" else None
    )


# ---------------------------------------------------------------------------
# 3. Eixo do rio + parametros de secao
# ---------------------------------------------------------------------------

st.subheader("3. Eixo do rio")
p1, p2, p3 = st.columns(3)
espac = p1.number_input("Espaçamento entre seções (m)", 5.0, 1000.0, 50.0, 5.0)
larg = p2.number_input("Largura das seções (m)", 10.0, 3000.0, 200.0, 10.0)
namo = int(p3.number_input("Amostras por seção", 10, 400, 60, 10))

import folium  # noqa: E402, PLC0415
from folium.plugins import Draw  # noqa: E402, PLC0415
from streamlit_folium import st_folium  # noqa: E402, PLC0415

col_map, col_ctrl = st.columns([4, 1])
with col_ctrl:
    st.caption(
        "Desenhe **1 polilinha** seguindo o eixo do rio (ícone de polilinha no "
        "mapa). Pode ir de jusante p/ montante ou o contrário — o sentido é "
        "detectado pelo thalweg."
    )
    calcular = st.button(
        "🌊 Gerar seções e calcular", type="primary", use_container_width=True,
    )

with col_map:
    m = folium.Map(location=center, zoom_start=15)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite",
    ).add_to(m)
    Draw(
        draw_options={
            "polyline": {"shapeOptions": {"color": "#1f77b4", "weight": 3}},
            "polygon": False, "rectangle": False, "circle": False,
            "marker": False, "circlemarker": False,
        },
        edit_options={"edit": True, "remove": True},
        export=False,
    ).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    saida = st_folium(
        m, height=520, width=None, returned_objects=["all_drawings"],
        key="mapa_inundacao", use_container_width=True,
    )
if saida and saida.get("all_drawings") is not None:
    st.session_state["inu_drawings"] = saida["all_drawings"]


if calcular:
    drawings = st.session_state.get("inu_drawings") or []
    linhas = [
        d["geometry"]["coordinates"]
        for d in drawings
        if d.get("geometry", {}).get("type") == "LineString"
        and len(d["geometry"]["coordinates"]) >= 2
    ]
    if len(linhas) != 1:
        st.error(
            f"Desenhe exatamente **1 polilinha** para o eixo (encontrei {len(linhas)})."
        )
    else:
        eixo_ll = [(c[0], c[1]) for c in linhas[0]]
        try:
            with st.spinner("Gerando seções, resolvendo a linha d'água e projetando a mancha..."):
                ger = inu.gerar_secoes_do_eixo(
                    eixo_ll, mdt_fonte,
                    espacamento_m=espac, largura_m=larg, n_amostras=namo,
                )
                perfil = inu.perfil_linha_dagua(
                    ger.secoes, Q=Q, n=n_manning,
                    cc_jusante=cc, cota_jusante_m=cota_jus,
                )
                mancha = inu.projetar_mancha(
                    ger.secoes, perfil, mdt_fonte, crs_secoes=ger.crs_trabalho,
                )
            st.session_state["inu_resultado"] = {
                "ger": ger, "perfil": perfil, "mancha": mancha,
            }
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha no cálculo: {exc}")


res = st.session_state.get("inu_resultado")
if not res:
    st.info("Desenhe o eixo e clique em **Gerar seções e calcular**.")
    st.stop()


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

ger = res["ger"]
perfil = res["perfil"]

for a in ger.avisos:
    st.warning(a)
if ger.sentido_invertido:
    st.caption(
        "ℹ Sentido do eixo invertido automaticamente — jusante detectada na "
        "ponta de menor thalweg."
    )
for w in perfil.warnings:
    st.warning(w)

df = perfil.to_dataframe()
mancha = res["mancha"]
for a in mancha.avisos:
    st.warning(a)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Seções", len(ger.secoes))
m2.metric("Área alagada", f"{mancha.area_alagada_m2 / 1e4:.2f} ha")
m3.metric("Prof. máx", f"{mancha.prof_max_m:.2f} m")
m4.metric("Prof. média", f"{mancha.prof_media_m:.2f} m")
fr_validos = df["Fr"].dropna()
m5.metric("Fr máx", f"{fr_validos.max():.2f}" if len(fr_validos) else "—")

# --- Mancha de inundacao sobre o MDT (produto principal) ---
st.subheader("🌊 Mancha de inundação sobre o MDT")
st.caption(
    f"Profundidade = linha d'água − terreno, recorte a {mancha.res_m:.1f} m. "
    "Relevo sombreado (hillshade colorido) = terreno; azul = lâmina inundada."
)
st.plotly_chart(plots.fig_mancha_hillshade(mancha), use_container_width=True)

with st.expander("Ver no mapa de satélite (interativo)"):
    mm = folium.Map(location=center, zoom_start=16)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite",
    ).add_to(mm)
    w_, s_, e_, n_ = mancha.bounds_lonlat
    rgba = _prof_rgba(mancha)
    if rgba is not None:
        folium.raster_layers.ImageOverlay(
            image=rgba, bounds=[[s_, w_], [n_, e_]], opacity=0.75, name="Profundidade",
        ).add_to(mm)
    folium.GeoJson(
        mancha.mancha_geojson, name="Contorno da mancha",
        style_function=lambda _: {"color": "#08519c", "weight": 1.5, "fillOpacity": 0.0},
    ).add_to(mm)
    folium.LayerControl(collapsed=False).add_to(mm)
    st_folium(mm, height=480, use_container_width=True, key="mapa_mancha")

with st.expander("⬇ Exportar (planta: mancha, eixo, seções)"):
    try:
        st.download_button(
            "⬇ GeoPackage completo — mancha + eixo + seções (ArcGIS/QGIS)",
            data=_pacote_geopackage(ger, perfil, mancha),
            file_name="inundacao_planta.gpkg",
            mime="application/geopackage+sqlite3",
            use_container_width=True,
            help="3 camadas em EPSG:4326, prontas pra montar a planta.",
        )
    except Exception as exc:  # noqa: BLE001
        st.caption(f"GeoPackage indisponível: {exc}")
    st.download_button(
        "⬇ GeoJSON (só o contorno da mancha, EPSG:4326)",
        data=mancha.mancha_geojson.encode("utf-8"),
        file_name="mancha_inundacao.geojson", mime="application/geo+json",
        use_container_width=True,
    )
    ce1, ce2 = st.columns(2)
    with ce1:
        try:
            st.download_button(
                "⬇ Shapefile (.zip)", data=_mancha_shapefile_zip(mancha),
                file_name="mancha_inundacao_shp.zip", mime="application/zip",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Shapefile indisponível: {exc}")
    with ce2:
        try:
            st.download_button(
                "⬇ GeoTIFF de profundidade", data=_profundidade_geotiff(mancha),
                file_name="profundidade_inundacao.tif", mime="image/tiff",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"GeoTIFF indisponível: {exc}")

st.subheader("Perfil longitudinal da linha d'água")
st.plotly_chart(plots.plot_perfil_inundacao(perfil), use_container_width=True)

st.subheader("Resultados por seção")
st.dataframe(df, use_container_width=True, hide_index=True)
