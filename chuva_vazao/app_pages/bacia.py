"""Página 0: delineamento automático de bacia pelo exutório."""
from __future__ import annotations

import os
from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from chuva_vazao import basin


st.title("0. Bacia de Contribuição")
st.caption(
    "Clique no mapa para marcar o exutório e delinear a bacia automaticamente "
    "(WhiteboxTools + DEM Copernicus ou local). As métricas resultantes podem "
    "ser aplicadas à Página 3 (Chuva-Vazão) para cálculo de tc e área."
)


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------

default_lat = float(st.session_state.get("exutorio_lat", -22.68))
default_lon = float(st.session_state.get("exutorio_lon", -44.32))

st.subheader("Fonte do DEM")
st.info(
    "Ordem importa: marque o exutório no mapa **primeiro** (clique ou ajuste "
    "Lat/Lon abaixo) e só então baixe o DEM. O recorte do DEM é centrado no "
    "exutório atual — baixar antes deixa o raster na região errada e o "
    "delineamento falha.",
    icon="⚠️",
)
fonte_dem = st.radio(
    "Origem dos dados topográficos:",
    [
        "Google Earth Engine (Copernicus GLO-30) — recomendado",
        "Upload local (GeoTIFF)",
        "OpenTopography API (Copernicus GLO-30)",
    ],
    horizontal=False,
    help="Define de onde vem o relevo (MDE) usado para delinear a bacia. "
         "'Google Earth Engine' puxa o Copernicus GLO-30 (~30 m) sem cadastro — "
         "caminho recomendado. 'Upload local' aceita um GeoTIFF seu em qualquer "
         "sistema de coordenadas (reprojetado para UTM automaticamente). "
         "'OpenTopography' também entrega Copernicus 30 m (ou outros produtos), "
         "mas exige uma API key gratuita. A fonte muda só a procedência e a "
         "resolução do dado, não a física do delineamento.",
)

dem_path: Path | None = None
if fonte_dem.startswith("Google"):
    col1, col2 = st.columns(2)
    buffer = col1.number_input(
        "Buffer (graus)", 0.02, 1.0, 0.1, 0.01, key="gee_buffer",
        help="Margem ao redor do exutório usada para recortar o DEM. O recorte é "
             "um quadrado de lado 2×buffer centrado no exutório (1° ≈ 111 km), "
             "então a área útil a montante é de cerca de buffer×111 km em cada "
             "direção. Buffer maior baixa um raster maior e mais lento, mas evita "
             "cortar a bacia na borda; buffer pequeno demais trunca bacias grandes "
             "e subestima área, perímetro e comprimento de canal (dispara o aviso "
             "de truncamento).",
    )
    col1.caption(
        "O exutório fica no CENTRO do recorte, então a bacia (que cresce a "
        "montante) tem ~buffer×111 km de folga em cada direção. Ordem de grandeza "
        "da área que cabe sem truncar (varia muito com a forma da bacia): "
        "0,05°→~30 km²; 0,1°→~100 km²; 0,2°→~500 km²; 0,3°→~1000 km²; "
        "0,5°→~3000 km². No Brasil (lat ~−23) a longitude rende ~102 km/°, um "
        "pouco menos no sentido leste-oeste. Se aparecer o aviso de truncamento, "
        "aumente o buffer."
    )
    download_btn = col2.button("Baixar DEM via GEE")
    if download_btn:
        with st.spinner(f"Puxando Copernicus GLO-30 via GEE (buffer={buffer}°)..."):
            try:
                dem_path = basin.download_dem_gee(
                    lat=default_lat, lon=default_lon, buffer_deg=buffer,
                )
                st.session_state["_dem_path"] = str(dem_path)
                st.success(f"DEM salvo em {dem_path}")
            except Exception as exc:
                st.error(
                    f"Falhou: {exc}\n\n"
                    "Se for erro de autenticação, rode no terminal: "
                    "`uv run earthengine authenticate`"
                )
    if "_dem_path" in st.session_state:
        dem_path = Path(st.session_state["_dem_path"])
        st.caption(f"DEM em cache: {dem_path.name}")

elif fonte_dem.startswith("Upload"):
    up = st.file_uploader(
        "DEM local (.tif, qualquer CRS — será reprojetado para UTM)",
        type=["tif", "tiff"],
        help="Carregue um DEM próprio em GeoTIFF (.tif/.tiff), em qualquer sistema "
             "de coordenadas — ele é reprojetado automaticamente para o UTM da "
             "região. A resolução do raster define o detalhe da rede de drenagem e "
             "das métricas. Aqui não há buffer: o recorte (e o risco de truncar a "
             "bacia) é a própria extensão do arquivo, então deixe folga a montante "
             "do exutório.",
    )
    if up is not None:
        cache_dir = Path(st.session_state.get("_dem_cache_dir", "data/dems"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        dem_path = cache_dir / up.name
        dem_path.write_bytes(up.read())
        st.success(f"DEM salvo em {dem_path}")
else:
    api_key = os.environ.get("OPENTOPO_API_KEY", "")
    if not api_key:
        api_key = st.text_input(
            "OpenTopography API key",
            type="password",
            help="Chave de acesso gratuita da API OpenTopography, usada só quando "
                 "essa fonte é escolhida. Não altera nenhum resultado de cálculo, "
                 "apenas libera o download do DEM. Crie em "
                 "https://portal.opentopography.org/. Se você definir "
                 "OPENTOPO_API_KEY no ambiente, este campo nem aparece.",
        )
    col1, col2, col3 = st.columns(3)
    buffer = col1.number_input(
        "Buffer (graus)", 0.02, 1.0, 0.1, 0.01, key="opt_buffer",
        help="Idêntico ao Buffer da opção GEE: define o quadrado de lado 2×buffer "
             "centrado no exutório (1° ≈ 111 km) que será baixado do "
             "OpenTopography. A área útil a montante é ~buffer×111 km em cada "
             "direção. Maior cobre bacias maiores sem truncar, ao custo de "
             "download mais lento; pequeno demais corta o divisor de águas e "
             "subestima as métricas.",
    )
    col1.caption(
        "O exutório fica no CENTRO do recorte, então a bacia (que cresce a "
        "montante) tem ~buffer×111 km de folga em cada direção. Ordem de grandeza "
        "da área que cabe sem truncar (varia muito com a forma da bacia): "
        "0,05°→~30 km²; 0,1°→~100 km²; 0,2°→~500 km²; 0,3°→~1000 km²; "
        "0,5°→~3000 km². No Brasil (lat ~−23) a longitude rende ~102 km/°. Aviso "
        "de truncamento = aumente o buffer."
    )
    dem_type = col2.selectbox(
        "Tipo", ["COP30", "COP90", "SRTMGL1", "SRTMGL3"],
        help="Produto e resolução do relevo no OpenTopography. COP30 (Copernicus "
             "30 m) e SRTMGL1 (SRTM 30 m) têm pixel de 30 m; COP90 e SRTMGL3 têm "
             "90 m. Resolução de 30 m capta melhor canais e divisores em bacias "
             "pequenas/urbanas; 90 m só compensa em bacias grandes, para baixar "
             "mais rápido.",
    )
    col2.caption(
        "30 m (COP30/SRTMGL1) para bacias de até dezenas de km²; 90 m "
        "(COP90/SRTMGL3) apenas acima de centenas de km², onde o detalhe pesa "
        "menos. O Copernicus (COP) costuma ter menos artefatos/vazios que o SRTM "
        "na mesma resolução. Lembre que o 'Threshold canal' é em células: com 90 m "
        "cada célula vale ~9× mais área."
    )
    download_btn = col3.button("Baixar DEM")
    if download_btn:
        if not api_key:
            st.error("Informe a API key.")
        else:
            with st.spinner(f"Baixando DEM {dem_type} para buffer={buffer}°..."):
                try:
                    dem_path = basin.download_dem_opentopography(
                        lat=default_lat, lon=default_lon,
                        buffer_deg=buffer, dem_type=dem_type,
                        api_key=api_key,
                    )
                    st.session_state["_dem_path"] = str(dem_path)
                    st.success(f"DEM salvo em {dem_path}")
                except Exception as exc:
                    st.error(f"Falhou: {exc}")
    if "_dem_path" in st.session_state:
        dem_path = Path(st.session_state["_dem_path"])
        st.caption(f"DEM em cache: {dem_path.name}")


# ---------------------------------------------------------------------------
# Mapa para clicar exutorio
# ---------------------------------------------------------------------------

st.subheader("Mapa")
col1, col2 = st.columns([3, 1])
with col2:
    lat = st.number_input(
        "Lat", -90.0, 90.0, default_lat, 0.001, format="%.6f",
        help="Latitude do exutório em graus decimais (WGS84/EPSG:4326); negativo = "
             "hemisfério Sul. Define o ponto de saída da bacia e o centro do "
             "recorte do DEM. Em geral é preenchida sozinha ao clicar no mapa.",
    )
    lon = st.number_input(
        "Lon", -180.0, 180.0, default_lon, 0.001, format="%.6f",
        help="Longitude do exutório em graus decimais (WGS84/EPSG:4326); negativo = "
             "a oeste de Greenwich. Junto com a latitude, fixa o exutório e o "
             "centro do recorte do DEM. Costuma vir do clique no mapa.",
    )
    snap_dist = st.number_input(
        "Snap (m)", 50, 2000, 500, 50,
        help="Raio máximo, em metros, dentro do qual o exutório clicado é arrastado "
             "até encostar no canal (rio de maior acumulação ou stream mais "
             "próximo, conforme o método). Maior tolera clique impreciso, mas pode "
             "grudar no rio errado; menor exige clicar quase em cima do canal, sob "
             "risco de a bacia sair vazia. A distância é em metros porque o DEM é "
             "reprojetado para UTM antes do snap.",
    )
    st.caption(
        "DEM ~30 m: 100–300 m (≈3–10 pixels) bastam para um clique bem posicionado; "
        "500–1000 m para clique grosseiro ou rio largo. Acima disso cresce o risco "
        "de pular para um canal vizinho. Ordem de grandeza."
    )
    stream_thresh = st.number_input(
        "Threshold canal (células)", 10, 10000, 100, 10,
        help="Quantas células de drenagem precisam se acumular num pixel para ele "
             "virar canal (WhiteboxTools ExtractStreams). Com DEM de 30 m, 100 "
             "células ≈ 0,09 km² de área de cabeceira. Maior = rede mais esparsa, "
             "canais começam mais a jusante; menor = rede mais densa, mas pode "
             "criar córregos espúrios e mudar onde o exutório faz snap. Se a bacia "
             "sair vazia, reduza o valor.",
    )
    st.caption(
        "DEM 30 m (célula = 900 m²): 10 céls ≈ 0,009 km²; 100 ≈ 0,09 km²; "
        "1000 ≈ 0,9 km²; 10000 ≈ 9 km². Para DEM de 90 m multiplique a área por ~9. "
        "Cabeceira de canal real costuma iniciar na ordem de 0,05–0,5 km² (≈55–550 "
        "células a 30 m), variando com clima e relevo."
    )
    snap_metodo = st.selectbox(
        "Snap do exutório",
        ["Maior acumulação (rio principal)", "Canal mais próximo (Jenson)"],
        help="Como ajustar o exutório ao canal antes de delinear. 'Maior "
             "acumulação' move para o pixel de maior área drenada dentro do raio "
             "de snap (rio principal, robusto ao threshold). 'Jenson' move para o "
             "canal mais próximo, respeitando melhor o clique, mas pode grudar num "
             "afluente pequeno se o threshold de canal estiver baixo.",
    )
    snap_method = "accumulation" if snap_metodo.startswith("Maior") else "jenson"

with col1:
    m = folium.Map(location=[lat, lon], zoom_start=13, tiles="OpenStreetMap")
    folium.TileLayer("OpenTopoMap", name="OpenTopoMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite", overlay=False,
    ).add_to(m)
    folium.Marker(
        [lat, lon], tooltip="Exutório (proposto)",
        icon=folium.Icon(color="blue", icon="tint", prefix="fa"),
    ).add_to(m)

    # Se ja houver bacia delineada, mostra
    bres = st.session_state.get("basin_result")
    if bres is not None:
        folium.GeoJson(
            bres["basin_geojson"],
            name="Bacia",
            style_function=lambda _: {"color": "#ff7f0e", "weight": 2, "fillOpacity": 0.25},
        ).add_to(m)
        folium.GeoJson(
            bres["stream_geojson"],
            name="Rede de drenagem",
            style_function=lambda _: {"color": "#1f77b4", "weight": 1.5},
        ).add_to(m)
        snapped = bres["outlet_snapped"]
        folium.Marker(
            [snapped[1], snapped[0]],
            tooltip="Exutório (snapped)",
            icon=folium.Icon(color="red", icon="water", prefix="fa"),
        ).add_to(m)
        m.fit_bounds(bres["bounds"])

    folium.LayerControl(collapsed=True).add_to(m)

    mapa_click = st_folium(
        m, height=450, width=None, returned_objects=["last_clicked"],
        key="mapa_bacia",
    )
    if mapa_click and mapa_click.get("last_clicked"):
        st.session_state.exutorio_lat = mapa_click["last_clicked"]["lat"]
        st.session_state.exutorio_lon = mapa_click["last_clicked"]["lng"]
        st.rerun()


# ---------------------------------------------------------------------------
# Delineamento
# ---------------------------------------------------------------------------

col1, col2 = st.columns([1, 4])
with col1:
    delinear = st.button("Delinear bacia", type="primary", disabled=dem_path is None)
with col2:
    if dem_path is None:
        st.warning("Carregue um DEM local ou baixe via OpenTopography antes.")

if delinear and dem_path is not None:
    with st.status("Executando pipeline WhiteboxTools...", expanded=True) as status:
        try:
            st.write("Reprojetando DEM para UTM...")
            result = basin.delineate_basin(
                lat=lat, lon=lon, dem_path=dem_path,
                snap_dist_m=snap_dist, stream_threshold=int(stream_thresh),
                snap_method=snap_method,
            )
            st.write("Delineamento concluído.")
            status.update(label="Bacia delineada ✓", state="complete")
        except Exception as exc:
            status.update(label=f"Falhou: {exc}", state="error")
            st.stop()

    # Salvar no session_state (convertendo para formato serializavel)
    st.session_state["basin_result"] = {
        "basin_geojson": result.basin_gdf.to_json(),
        "stream_geojson": result.stream_gdf.to_json(),
        "outlet_snapped": (result.outlet_snapped.x, result.outlet_snapped.y),
        "outlet_original": (result.outlet_original.x, result.outlet_original.y),
        "bounds": [
            [result.basin_gdf.total_bounds[1], result.basin_gdf.total_bounds[0]],
            [result.basin_gdf.total_bounds[3], result.basin_gdf.total_bounds[2]],
        ],
        "metrics": result.metrics.summary_dict(),
        "clipped_by_dem": result.clipped_by_dem,
        "snap_method": snap_method,
    }
    st.session_state["basin_metrics"] = result.metrics
    st.rerun()


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

bres = st.session_state.get("basin_result")
metrics = st.session_state.get("basin_metrics")
if bres is not None and metrics is not None:
    st.divider()
    if bres.get("clipped_by_dem"):
        st.warning(
            "A bacia encosta na borda do DEM — provavelmente está **truncada** "
            "(aquele lado reto é o limite do raster baixado, não o divisor de águas "
            "real). Como o exutório fica a jusante, a bacia se estende bastante numa "
            "direção; **aumente o Buffer (graus)** na Fonte do DEM e baixe de novo. "
            "Enquanto isso, **área, perímetro e L do canal estão subestimados**.",
            icon="⚠️",
        )
    st.subheader("Métricas da bacia")
    cols = st.columns(4)
    d = bres["metrics"]
    cols[0].metric("Área", f"{d['A (km2)']:g} km²")
    cols[1].metric("Perímetro", f"{d['P (km)']:g} km")
    cols[2].metric("L canal", f"{d['L canal (km)']:g} km")
    cols[3].metric(
        "S canal", f"{d['S media (%)']:g} %",
        help="Declividade do canal principal = ΔH / L_canal. Não é declividade média dos pixels.",
    )

    cols = st.columns(4)
    cols[0].metric("Z máx", f"{d['Z max (m)']:g} m")
    cols[1].metric("Z mín", f"{d['Z min (m)']:g} m")
    cols[2].metric(
        "ΔH talvegue", f"{d['dH talvegue (m)']:g} m",
        help=f"Queda ao longo do talvegue (nascente→exutório), usada no tc. "
             f"Amplitude altimétrica da bacia (Zmáx−Zmín) = {d['dH bacia (m)']:g} m.",
    )
    metodo_rec = (
        "Racional" if metrics.area_km2 <= 2
        else "SCS-HU" if metrics.area_km2 <= 250
        else "Distribuído"
    )
    cols[3].metric(
        "Método recomendado", metodo_rec,
        help=("Acima de 250 km² o recomendado é o modelo Distribuído (sub-bacias "
              "+ Muskingum-Cunge), disponível na Página 3."
              if metrics.area_km2 > 250 else None),
    )

    if st.button("Aplicar na Página 3 (Chuva-Vazão)", type="primary"):
        st.session_state.area_km2 = float(metrics.area_km2)
        # L_km e H_m para calculo de tc. H_m usa a queda ao longo do TALVEGUE
        # (nascente->exutorio), nao a amplitude Zmax-Zmin da bacia — esta ultima
        # inclui o topo do divisor e superestima a declividade do canal.
        dh_tc = metrics.delta_h_talvegue_m or metrics.delta_h_m
        st.session_state["bacia_L_km"] = float(metrics.flowlength_km)
        st.session_state["bacia_H_m"] = float(dh_tc)
        nota_dh = ""
        if metrics.delta_h_talvegue_m and metrics.delta_h_talvegue_m > 0:
            nota_dh = (
                f" (ΔH do talvegue; amplitude da bacia = "
                f"{metrics.delta_h_m:.0f} m)"
            )
        st.success(
            f"Bacia aplicada: A = {metrics.area_km2:.3f} km², "
            f"L = {metrics.flowlength_km:.3f} km, ΔH = {dh_tc:.1f} m{nota_dh}. "
            "Vá para a Página 3 para calcular tc e Q."
        )
