from typing import Union, Any, List, Dict, Optional
import pandas as pd
import geopandas as gpd
import itertools
from src.settings import *
import json5 as json
import numpy as np
from src.tools.logger import logging, get_logger
import swifter
from src.tools.configs import DatasetGenerationConfig
from dataclasses import dataclass

logger = get_logger(__name__)
sparse_dtype = pd.SparseDtype(int, fill_value=0)

with open(RAW_DATA_DIR / "implicit_maxspeeds.jsonc", "r") as f:
    IMPLICIT_MAXSPEEDS = json.load(f)


@dataclass
class SpatialDataset:
    config: DatasetGenerationConfig
    cities: pd.DataFrame
    edges: gpd.GeoDataFrame
    edges_feature_selected: gpd.GeoDataFrame
    hexagons: gpd.GeoDataFrame
    hex_agg: Optional[pd.DataFrame]
    hex_agg_normalized: Optional[pd.DataFrame]


def features_wide_to_long(df_w: pd.DataFrame, feature_keys: List[str]) -> pd.DataFrame:
    df_l = df_w.copy()
    for f_k in feature_keys:
        features_for_key = [x for x in df_l.columns if f_k in x]
        df_l[f_k] = df_l[features_for_key].idxmax(axis=1).astype("category")
        df_l[f_k][df_l[features_for_key].sum(axis=1) == 0] = None
        df_l.drop(columns=features_for_key, inplace=True)
    return df_l


def normalize_df(hex_agg: pd.DataFrame, type="global") -> pd.DataFrame:
    if type == "global":
        df = (hex_agg / hex_agg.max()).fillna(0.0)
    elif type == "local":
        df = (hex_agg / hex_agg.groupby(level=["continent", "country", "city"]).max()).fillna(0.0)
    else:
        raise ValueError("type must be either 'global' or 'local'")

    return df

def apply_feature_selection(edges: Union[pd.DataFrame, gpd.GeoDataFrame], features_config: dict, scale_length=False) -> pd.DataFrame:
    features_merge: List[dict] = features_config["settings"]["merge"]
    features_selected: Dict[str, List[str]] = features_config["features"]

    edges_after_merge = apply_features_mapping(edges, features_merge)
    features = [f"{k}_{v}" for k, vs in features_selected.items() for v in vs]
    if scale_length:
        edges_after_merge = edges_after_merge \
            .reset_index(drop=True) \
            .swifter.apply(lambda x: x[features] * x["length"], axis=1)
        edges_after_merge.index = edges.index
        # edges_after_merge[f] = edges_after_merge[f].astype("float32")

    edges_after_assume = apply_features_assume(edges_after_merge[features], features_config["settings"]["assume"])
    return edges_after_assume


def apply_features_mapping(edges: Union[pd.DataFrame, gpd.GeoDataFrame], features_merge: List[dict]) -> pd.DataFrame:
    edges_copy = edges.copy()
    for feature_merge in features_merge:
        feature_name = feature_merge["feature"]
        feature_mapping = feature_merge["mapping"]

        for feature_source, feature_target in feature_mapping.items():
            feature_source = f"{feature_name}_{feature_source}"
            feature_target = f"{feature_name}_{feature_target}"
            
            if feature_source not in edges_copy:
                edges_copy[feature_source] = 0
            if feature_target not in edges_copy:
                edges_copy[feature_target] = 0

            edges_copy[feature_target] = ((edges_copy[feature_source] == 1) | (edges_copy[feature_target] == 1)).astype("int32")
            edges_copy = edges_copy.drop(columns=feature_source)

    return edges_copy


def apply_features_assume(edges: Union[pd.DataFrame, gpd.GeoDataFrame], features_assume: dict) -> pd.DataFrame:
    for feature_name, feature_val in features_assume.items():
        feature_col = f"{feature_name}_{feature_val}"
        feature_cols = [col for col in edges.columns if feature_name in col]
        edges = edges.assign(**{feature_col: ((edges[feature_col].astype(bool)) | (edges[feature_cols].sum(axis=1) == 0)).astype(int)})

    return edges



def generate_features_for_edges(
    edges: Union[pd.DataFrame, gpd.GeoDataFrame], featureset: Dict[str, List[str]]
) -> gpd.GeoDataFrame:
    for feature in featureset.keys():
        if feature in edges:
            edges = edges.join(explode_and_pivot(edges, feature))

    feature_column_names = list(
        itertools.chain(*[[f"{k}_{v}" for v in vs] for k, vs in featureset.items()])
    )
    columns_to_keep = ["u", "v", "key", "id", "h3_id", "geometry"] + feature_column_names
    columns_to_drop = list(set(edges.columns) - set(columns_to_keep))
    columns_to_add = list(
        set(columns_to_keep) - (set(columns_to_keep).intersection(set(edges.columns)))
    )

    edges.drop(columns=columns_to_drop, inplace=True)

    for c in columns_to_add:
        edges[c] = 0

    edges = edges.reindex(columns_to_keep, axis=1)

    return gpd.GeoDataFrame(edges)  # type: ignore


def explode_and_pivot(
    df: Union[pd.DataFrame, gpd.GeoDataFrame], column_name: str
) -> pd.DataFrame:
    prefix = f"{column_name}_"
    new_column_name = f"{prefix}new"
    df[new_column_name] = df[column_name].apply(
        lambda x: preprocess_and_convert_to_list(str(x), column_name)
    )
    df_expl = df.explode(new_column_name)  # type: ignore
    # df_expl[new_column_name] = df_expl[new_column_name].astype(str)  # type: ignore
    df_piv = df_expl.pivot(columns=new_column_name, values=new_column_name).add_prefix(
        prefix
    )
    df_piv[df_piv.notnull()] = 1
    df_piv = df_piv.fillna(0).astype('int32')

    return df_piv


def melt_and_max(
    edges: gpd.GeoDataFrame, column_name: str, columns: List[str]
) -> pd.Series:
    gdf = edges.groupby("id").first().drop(columns="h3_id").reset_index()
    gdf = gdf[["id"] + columns].melt(id_vars="id", value_vars=columns)
    gdf["variable"] = gdf["variable"].apply(lambda x: float(x.split("_")[1]))
    gdf["mul"] = gdf["variable"] * gdf["value"]
    gdf = gdf.groupby("id").max()[["mul"]].rename(columns={"mul": column_name})

    return gdf


def preprocess_and_convert_to_list(x: str, column_name: str) -> List[str]:
    x_list = eval(x) if "[" in x else [x]
    x_list = [sanitize_and_normalize(x, column_name) for x in x_list]

    return list(set(x_list))
    

def sanitize_and_normalize(x: str, column_name: str) -> str:
    return normalize(sanitize(x, column_name), column_name)


def normalize(x: str, column_name: str) -> str:
    try:
        if x == "None":
            return x
        elif column_name == "lanes":
            x = min(int(x) , 15)
        elif column_name == "maxspeed":
            x = float(x)
            if x <= 5:
                x = 5
            elif x <= 7:
                x = 7
            elif x <= 10:
                x = 10
            elif x <= 15:
                x = 15
            else:
                x = min(int(round(x / 10) * 10), 200)
        elif column_name == "width":
            x = min(round(float(x) * 2) / 2, 30.0)
    except Exception as e:
        logger.warn(f"{column_name}: {x} - {type(x)} | {e}")
        return "None"

    return str(x)


def sanitize(x: str, column_name: str) -> str:
    if x in ["", "none", "None"]:
        return "None"

    try:
        if column_name == "lanes":
            x = int(float(x))
        elif column_name == "maxspeed":
            if x in ("signals", "variable"):
                return "None"

            x = IMPLICIT_MAXSPEEDS[x] if x in IMPLICIT_MAXSPEEDS else x
            x = x.replace("km/h", "")
            if "mph" in x:
                x = float(x.split(" mph")[0])
                x = x * 1.6
            x = float(x)
        elif column_name == "width":
            if x.endswith(" m") or x.endswith("m") or x.endswith("meter"):
                x = x.split("m")[0].strip()
            elif "'" in x:
                x = float(x.split("'")[0])
                x = x * 0.0254
            elif x.endswith("ft"):
                x = float(x.split(" ft")[0])
                x = x * 0.3048
            x = float(x)

    except Exception as e:
        logger.warn(f"{column_name}: {x} - {type(x)} | {e}")
        # raise Exception()
        return "None"

    return str(x)
